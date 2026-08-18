"""Nuvel Skills MCP stdio server (JSON-RPC 2.0).

Exposes a Nuvel Skills hub as MCP resources and tools over stdio. Stdlib only.

Protocol channel: stdout (one JSON message per line).
Logging channel:  stderr.

Resources:
    skill://{theme}/{name}   Full SKILL.md content for a skill.

Tools:
    search_skills        Search skills by keyword in name/description.
    get_skill            Fetch a skill's full content + metadata by name.
    propose_improvement  File a structured GitHub issue proposing a skill fix.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import IO

from nuvel.mcp.skills_loader import SkillsError, SkillsLoader, parse_frontmatter

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "nuvel-skills", "version": "1.0.0"}

# JSON-RPC standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Skill-improvement issues are filed against the skills hub.
GITHUB_REPO = "Folken2/skills"
GITHUB_ISSUES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/issues"


def log(*args) -> None:
    """Diagnostics go to stderr so stdout stays a clean protocol channel."""
    print(*args, file=sys.stderr, flush=True)


class McpError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _parse_skill_uri(uri: str) -> tuple[str, str]:
    """Parse ``skill://{theme}/{name}`` -> (theme, name) or raise ValueError."""
    prefix = "skill://"
    if not uri.startswith(prefix):
        raise ValueError("URI must start with 'skill://'")
    parts = uri[len(prefix):].split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("URI must be 'skill://{theme}/{name}'")
    return parts[0], parts[1]


def _summarize(text: str, limit: int = 60) -> str:
    """One-line summary for an issue title (first line, truncated)."""
    stripped = (text or "").strip()
    first = stripped.splitlines()[0] if stripped else ""
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return first


def _improvement_issue(skill_name, current_version, issue, suggested_fix, harness):
    """Build the (title, body) for a skill-improvement GitHub issue."""
    title = "[Skill Improvement] {} v{} — {}".format(
        skill_name, current_version, _summarize(issue) or "improvement"
    )
    body = (
        "## Skill Improvement Proposal\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        "| **Skill** | `{skill}` |\n"
        "| **Current version** | `{version}` |\n"
        "| **Harness** | `{harness}` |\n\n"
        "### Issue — what went wrong or changed\n\n"
        "{issue}\n\n"
        "### Suggested fix\n\n"
        "{fix}\n\n"
        "---\n"
        "_Filed via the `propose_improvement` MCP tool (`nuvel mcp serve`). "
        "A curator will review and merge accepted improvements._\n"
    ).format(
        skill=skill_name,
        version=current_version,
        harness=harness,
        issue=issue.strip(),
        fix=suggested_fix.strip(),
    )
    return title, body


class SkillsMCPServer:
    """MCP JSON-RPC server backed by a :class:`SkillsLoader`."""

    def __init__(self, loader: SkillsLoader, theme: str | None = None):
        self.loader = loader
        # When set, discovery (resources/list, search_skills, get_skill) is
        # scoped to this single theme. resources/read is unaffected — scoping
        # is about discovery, not access control.
        self.theme = theme
        self._methods = {
            "initialize": self.handle_initialize,
            "resources/list": self.handle_resources_list,
            "resources/read": self.handle_resources_read,
            "tools/list": self.handle_tools_list,
            "tools/call": self.handle_tools_call,
        }
        self._tools = {
            "search_skills": self.tool_search_skills,
            "get_skill": self.tool_get_skill,
            "propose_improvement": self.tool_propose_improvement,
        }

    # --- Scoping helpers -----------------------------------------------------

    def _scoped_entries(self):
        """Yield ``(theme, entry)`` honoring the configured theme scope."""
        for theme, entry in self.loader.iter_entries():
            if self.theme is not None and theme != self.theme:
                continue
            yield theme, entry

    # --- Method handlers -----------------------------------------------------

    def handle_initialize(self, params):
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "resources": {"list": True, "read": True},
                "tools": {"list": True, "call": True},
            },
            "serverInfo": SERVER_INFO,
        }

    def handle_resources_list(self, params):
        resources = []
        for theme, entry in self._scoped_entries():
            name = entry["name"]
            resources.append({
                "uri": f"skill://{theme}/{name}",
                "name": name,
                "description": entry.get("description", ""),
                "mimeType": "text/markdown",
            })
        return {"resources": resources}

    def handle_resources_read(self, params):
        uri = params.get("uri")
        if not uri:
            raise McpError(INVALID_PARAMS, "Missing required param 'uri'")
        try:
            theme, name = _parse_skill_uri(uri)
        except ValueError as exc:
            raise McpError(INVALID_PARAMS, f"Invalid skill URI: {exc}")

        theme, entry = self.loader.find(theme, name)
        if entry is None:
            raise McpError(INVALID_PARAMS, f"Skill not found: {uri}")
        try:
            content = self.loader.read_skill(theme, entry)
        except SkillsError:
            raise McpError(INVALID_PARAMS, f"SKILL.md not available for {uri}")

        return {
            "contents": [{
                "uri": uri,
                "mimeType": "text/markdown",
                "text": content,
            }]
        }

    def handle_tools_list(self, params):
        return {
            "tools": [
                {
                    "name": "search_skills",
                    "description": "Search skills by keyword in name or description.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Keyword to match against skill name/description.",
                            }
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "get_skill",
                    "description": "Get the full SKILL.md content and metadata for a skill by name or 'theme/name'.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Skill name (e.g. 'bug-triage') or 'theme/name'.",
                            }
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "propose_improvement",
                    "description": (
                        "Propose an improvement to a skill after using it and finding "
                        "it drifted (outdated tooling, missing edge cases, wrong "
                        "assumptions). Files a structured GitHub issue for a curator "
                        "to review."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Name of the skill being improved (e.g. 'bug-triage').",
                            },
                            "current_version": {
                                "type": "string",
                                "description": "Version from the SKILL.md frontmatter (e.g. '1.0.0').",
                            },
                            "issue": {
                                "type": "string",
                                "description": "What went wrong or what changed.",
                            },
                            "suggested_fix": {
                                "type": "string",
                                "description": "Proposed correction or addition.",
                            },
                            "harness": {
                                "type": "string",
                                "description": "Agent/harness you're running (e.g. 'claude-code', 'cursor', 'hermes').",
                            },
                        },
                        "required": ["skill_name", "current_version", "issue", "suggested_fix"],
                    },
                },
            ]
        }

    def handle_tools_call(self, params):
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        handler = self._tools.get(tool_name)
        if handler is None:
            raise McpError(INVALID_PARAMS, f"Unknown tool: {tool_name}")
        result = handler(args)
        # MCP tool results wrap output as a list of content blocks.
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False),
            }],
            "isError": False,
        }

    # --- Tools ---------------------------------------------------------------

    def tool_search_skills(self, args):
        query = (args.get("query") or "").strip().lower()
        if not query:
            raise McpError(INVALID_PARAMS, "Missing required argument 'query'")
        matches = []
        for theme, entry in self._scoped_entries():
            haystack = "{} {}".format(
                entry.get("name", ""), entry.get("description", "")
            ).lower()
            if query in haystack:
                matches.append({
                    "uri": f"skill://{theme}/{entry['name']}",
                    "theme": theme,
                    "name": entry["name"],
                    "description": entry.get("description", ""),
                    "author": entry.get("author", ""),
                })
        return {"query": query, "count": len(matches), "results": matches}

    def tool_get_skill(self, args):
        name = (args.get("name") or "").strip()
        if not name:
            raise McpError(INVALID_PARAMS, "Missing required argument 'name'")

        wanted_theme, wanted_name = (None, name)
        if "/" in name:
            wanted_theme, wanted_name = name.split("/", 1)

        # Under a theme scope, discovery is confined to that theme.
        if self.theme is not None:
            if wanted_theme is not None and wanted_theme != self.theme:
                raise McpError(INVALID_PARAMS, f"Skill not found: {name}")
            wanted_theme = self.theme

        theme, entry = self.loader.find(wanted_theme, wanted_name)
        if entry is None:
            raise McpError(INVALID_PARAMS, f"Skill not found: {name}")

        try:
            content = self.loader.read_skill(theme, entry)
        except SkillsError:
            raise McpError(INVALID_PARAMS, f"SKILL.md not available for {name}")

        return {
            "uri": f"skill://{theme}/{entry['name']}",
            "theme": theme,
            "name": entry["name"],
            "description": entry.get("description", ""),
            "author": entry.get("author", ""),
            "metadata": parse_frontmatter(content),
            "content": content,
        }

    def tool_propose_improvement(self, args):
        skill_name = (args.get("skill_name") or "").strip()
        current_version = (args.get("current_version") or "").strip()
        issue = (args.get("issue") or "").strip()
        suggested_fix = (args.get("suggested_fix") or "").strip()
        harness = (args.get("harness") or "").strip() or "unknown"

        missing = [
            k
            for k, v in (
                ("skill_name", skill_name),
                ("current_version", current_version),
                ("issue", issue),
                ("suggested_fix", suggested_fix),
            )
            if not v
        ]
        if missing:
            raise McpError(
                INVALID_PARAMS,
                "Missing required argument(s): {}".format(", ".join(missing)),
            )

        title, body = _improvement_issue(
            skill_name, current_version, issue, suggested_fix, harness
        )

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            log("GitHub API not configured (no GITHUB_TOKEN). Improvement proposal:")
            log(f"  title: {title}")
            log(body)
            return {
                "status": "logged",
                "message": "GitHub API not configured — improvement logged to stderr instead",
                "title": title,
                "body": body,
            }

        payload = json.dumps(
            {"title": title, "body": body, "labels": ["skill-improvement"]}
        ).encode("utf-8")
        request = urllib.request.Request(
            GITHUB_ISSUES_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "nuvel-skills-mcp",
            },
        )
        try:
            with urllib.request.urlopen(request) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
            log(f"GitHub API error {exc.code}: {detail}")
            raise McpError(
                INTERNAL_ERROR, f"GitHub API returned {exc.code}: {detail}"
            )
        except urllib.error.URLError as exc:
            log(f"GitHub API request failed: {exc}")
            raise McpError(INTERNAL_ERROR, f"GitHub API request failed: {exc}")

        return {
            "status": "filed",
            "message": "Improvement proposal filed as GitHub issue",
            "issue_url": data.get("html_url"),
            "issue_number": data.get("number"),
            "title": title,
        }

    # --- Dispatch / loop -----------------------------------------------------

    def dispatch(self, message):
        """Handle one decoded JSON-RPC message. Returns a response dict or None."""
        msg_id = message.get("id")
        method = message.get("method")
        is_notification = "id" not in message

        if method is None:
            if is_notification:
                return None
            return error_response(msg_id, INVALID_REQUEST, "Missing 'method'")

        if method.startswith("notifications/") or method.startswith("$/"):
            # e.g. notifications/initialized, $/cancelRequest — no response.
            return None

        handler = self._methods.get(method)
        if handler is None:
            if is_notification:
                return None
            return error_response(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")

        params = message.get("params") or {}
        try:
            result = handler(params)
        except McpError as exc:
            log(f"McpError in {method}: {exc.message}")
            return error_response(msg_id, exc.code, exc.message)
        except SkillsError as exc:
            log(f"SkillsError in {method}: {exc}")
            return error_response(msg_id, INTERNAL_ERROR, f"Skills error: {exc}")
        except Exception as exc:  # noqa: BLE001 - report as JSON-RPC internal error
            log(f"Internal error in {method}: {exc}")
            return error_response(msg_id, INTERNAL_ERROR, f"Internal error: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def serve(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        """Run the stdio read/dispatch/write loop until EOF on stdin."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        log(f"nuvel-skills MCP server starting (skills_dir={self.loader.skills_dir})")
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                log(f"Parse error: {exc}")
                _write_message(stdout, error_response(None, PARSE_ERROR, "Parse error"))
                continue

            response = self.dispatch(message)
            if response is not None:
                _write_message(stdout, response)
        log("nuvel-skills MCP server: EOF on stdin, shutting down.")


def error_response(msg_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _write_message(stdout: IO[str], message) -> None:
    stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    stdout.flush()
