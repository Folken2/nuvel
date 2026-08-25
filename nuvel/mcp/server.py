"""Nuvel Skills MCP stdio server (JSON-RPC 2.0).

Exposes a Nuvel Skills hub as MCP resources and tools over stdio. Stdlib only.

Protocol channel: stdout (one JSON message per line).
Logging channel:  stderr.

Resources:
    skill://{theme}/{name}   Full SKILL.md content for a skill.

Tools:
    search_skills        Search skills by keyword in name/description.
    get_skill            Fetch a skill's full content + metadata by name, or a
                         single ``## section`` (section-scoped reading).
    fence_value          Wrap a value in provenance-fencing markers.
    propose_improvement  File a structured GitHub issue proposing a skill fix.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import IO

from nuvel.mcp.skills_loader import SkillsError, SkillsLoader, parse_frontmatter
from nuvel.mcp import feedback

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


# Airbyte-style template placeholder: {{ fiscal_year_summary }}.
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_][A-Za-z0-9_.-]*)\s*\}\}")


def detect_template_variables(text: str) -> list[str]:
    """Return the unique ``{{ var_name }}`` placeholders found in ``text``."""
    seen: list[str] = []
    for match in TEMPLATE_VAR_RE.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def fence_value(value: str, source: str) -> str:
    """Wrap a value in Airbyte-style provenance-fencing markers.

    The markers tell a reading agent that the enclosed value is a
    machine-discovered / user-supplied fact, not an instruction, so a poisoned
    value can't be mistaken for a directive.
    """
    begin = f"[begin customer-specific data ({source}, unverified)]"
    end = "[end customer-specific data]"
    return f"{begin}\n{value}\n{end}"


def extract_section(content: str, section: str) -> str | None:
    """Return the ``## {section}`` subsection of a SKILL.md body.

    Includes the heading and any sub-headings/body up to (but not including)
    the next ``##`` heading of the same level. Returns ``None`` when the
    section heading isn't found.
    """
    target = f"## {section}"
    lines = content.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == target:
            start = i
            break
    if start is None:
        return None
    collected: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("## ") and stripped != target:
            break
        collected.append(line)
    return "\n".join(collected).strip()


def _substitute(content: str, var: str, value: str) -> str:
    """Replace ``{{ var }}`` (any whitespace) with ``value`` in ``content``."""
    pattern = re.compile(r"\{\{\s*" + re.escape(var) + r"\s*\}\}")
    return pattern.sub(value, content)


def _memory_app_name() -> str:
    return os.environ.get("NUVEL_MCP_APP_NAME", "nuvel-skills")


def _memory_user_id() -> str:
    return os.environ.get("NUVEL_MCP_USER_ID", "default")


def _memory_kwargs(flag_value: str | None) -> dict:
    """Map the ``--with-org-memory`` value to OrgMemoryService factory kwargs.

    A value containing ``://`` is treated as a Postgres DSN; anything else is
    treated as an org-graph YAML path. ``None`` for a key lets the factory fall
    back to the corresponding environment variable.
    """
    if not flag_value:
        return {"dsn": None, "org_graph_path": None}
    if "://" in flag_value:
        return {"dsn": flag_value, "org_graph_path": None}
    return {"dsn": None, "org_graph_path": flag_value}


def _memory_response_text(response) -> str | None:
    """Best-effort plain-text extraction from an ADK SearchMemoryResponse.

    Uses ``getattr`` throughout so the stdlib-only module never has to import
    ``google.genai`` types directly.
    """
    memories = getattr(response, "memories", None) or []
    for memory in memories:
        content = getattr(memory, "content", None)
        parts = getattr(content, "parts", None) or []
        texts = [getattr(p, "text", "") for p in parts if getattr(p, "text", None)]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return None


async def _resolve_templates_async(content, variables, build_service, kwargs):
    """Resolve placeholders against a freshly-built OrgMemoryService (async)."""
    service = await build_service(migrate=False, **kwargs)
    resolved = content
    for var in variables:
        response = await service.search_memory(
            app_name=_memory_app_name(),
            user_id=_memory_user_id(),
            query=var.replace("_", " ").replace("-", " "),
            synthesize=False,
        )
        value = _memory_response_text(response)
        if value:
            resolved = _substitute(resolved, var, value)
    return resolved


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

    def __init__(
        self,
        loader: SkillsLoader,
        theme: str | None = None,
        require_filter: list[str] | None = None,
        with_org_memory: str | None = None,
    ):
        self.loader = loader
        # When set, discovery (resources/list, search_skills, get_skill) is
        # scoped to this single theme. resources/read is unaffected — scoping
        # is about discovery, not access control.
        self.theme = theme
        # When set, discovery filters to skills whose ``requires`` frontmatter
        # declares every capability in the list (Airbyte-style gating). A skill
        # with no ``requires`` is hidden when the filter is active because it
        # can't demonstrate the required capabilities.
        self.require_filter = require_filter
        # Optional DSN / org-graph path enabling OrgMemoryService template
        # resolution. Imported lazily so the default (stdlib-only) path never
        # touches ADK or asyncio.
        self.with_org_memory = with_org_memory
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
            "fence_value": self.tool_fence_value,
            "record_feedback": self.tool_record_feedback,
            "check_skill_health": self.tool_check_skill_health,
        }

    # --- Scoping helpers -----------------------------------------------------

    def _satisfies_requirements(self, theme: str, entry: dict) -> bool:
        """True when the entry's ``requires`` cover every gating capability."""
        if not self.require_filter:
            return True
        required = set(self.loader.entry_requires(theme, entry))
        return set(self.require_filter).issubset(required)

    def _scoped_entries(self):
        """Yield ``(theme, entry)`` honoring theme scope and requirements."""
        for theme, entry in self.loader.iter_entries():
            if self.theme is not None and theme != self.theme:
                continue
            if not self._satisfies_requirements(theme, entry):
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
                            },
                            "section": {
                                "type": "string",
                                "description": (
                                    "Optional section heading to read instead of the whole "
                                    "SKILL.md (e.g. 'Step-by-step instructions'). Returns "
                                    "only that subsection."
                                ),
                            },
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "fence_value",
                    "description": (
                        "Wrap a value in provenance-fencing markers so a "
                        "machine-discovered or user-supplied fact can't be mistaken "
                        "for an instruction."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "value": {
                                "type": "string",
                                "description": "The value to fence.",
                            },
                            "source": {
                                "type": "string",
                                "description": (
                                    "Where the value came from "
                                    "(e.g. 'machine-discovered', 'user-supplied')."
                                ),
                                "default": "machine-discovered",
                            },
                        },
                        "required": ["value"],
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
                {
                    "name": "record_feedback",
                    "description": (
                        "Record structured feedback after using a skill in the field. "
                        "Tracks outcome, severity, what worked and what didn't — used "
                        "by check_skill_health to surface quality trends."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Name of the skill this feedback is for.",
                            },
                            "skill_version": {
                                "type": "string",
                                "description": "Version from the SKILL.md frontmatter (or 'unknown').",
                            },
                            "outcome": {
                                "type": "string",
                                "enum": ["success", "partial", "failure", "blocked"],
                                "description": "How well the skill worked: success (completed task), partial (needed tweaks), failure (wrong), blocked (couldn't proceed).",
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["blocking", "misleading", "minor"],
                                "description": "Impact: blocking (can't use skill), misleading (wrong info), minor (cosmetic).",
                            },
                            "section": {
                                "type": "string",
                                "description": "Which ## heading(s) the feedback applies to, comma-separated.",
                            },
                            "what_worked": {
                                "type": "string",
                                "description": "What the skill got right (optional).",
                            },
                            "what_didnt": {
                                "type": "string",
                                "description": "What went wrong — missing steps, outdated commands, wrong assumptions.",
                            },
                            "proposed_patch": {
                                "type": "string",
                                "description": "Suggested fix or replacement text (optional).",
                            },
                            "harness": {
                                "type": "string",
                                "description": "Agent/harness you're running (e.g. 'hermes', 'claude-code'). Defaults to 'unknown'.",
                            },
                            "user_corrected": {
                                "type": "boolean",
                                "description": "Whether the user had to manually correct the skill's output.",
                            },
                            "attribution": {
                                "type": "string",
                                "description": "Who or what triggered the feedback (optional).",
                            },
                            "correlation_id": {
                                "type": "string",
                                "description": "External correlation id linking this feedback to a run/task (optional).",
                            },
                        },
                        "required": ["skill_name", "skill_version", "outcome", "severity", "what_didnt"],
                    },
                },
                {
                    "name": "check_skill_health",
                    "description": (
                        "Check a skill's health from its feedback history: outcome "
                        "counts, trend, flagged sections, and a recommendation "
                        "('ok', 'review_before_use', or 'use_cautiously')."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Name of the skill to check.",
                            },
                        },
                        "required": ["skill_name"],
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
        section = (args.get("section") or "").strip() or None

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

        metadata = parse_frontmatter(content)

        # Section-scoped reading: return only the requested subsection.
        body = content
        if section is not None:
            scoped = extract_section(content, section)
            if scoped is None:
                raise McpError(INVALID_PARAMS, f"Section not found: {section}")
            body = scoped

        # Template-variable detection (progressive disclosure of placeholders).
        variables = detect_template_variables(body)
        metadata["template_variables"] = variables

        result = {
            "uri": f"skill://{theme}/{entry['name']}",
            "theme": theme,
            "name": entry["name"],
            "description": entry.get("description", ""),
            "author": entry.get("author", ""),
            "metadata": metadata,
            "content": body,
        }
        if section is not None:
            result["section"] = section

        # Health signals from feedback history (best-effort, no feedback = "ok").
        result["health"] = feedback.compute_health(self.loader.skills_dir, entry["name"])

        # Optional OrgMemoryService resolution (ADK-backed, best-effort).
        if variables and self.with_org_memory:
            resolved = self._resolve_templates(body, variables)
            if resolved is not None:
                result["resolved_content"] = resolved

        return result

    def tool_fence_value(self, args):
        value = args.get("value")
        if not isinstance(value, str) or not value.strip():
            raise McpError(INVALID_PARAMS, "Missing required argument 'value'")
        source = (args.get("source") or "machine-discovered").strip()
        return {
            "value": fence_value(value, source),
            "source": source,
        }

    def tool_record_feedback(self, args):
        """Record structured feedback after a skill is used."""
        return feedback.write_feedback(self.loader.skills_dir, args)

    def tool_check_skill_health(self, args):
        """Check a skill's health from its feedback history."""
        skill_name = (args.get("skill_name") or "").strip()
        if not skill_name:
            raise McpError(INVALID_PARAMS, "Missing required argument 'skill_name'")
        return feedback.compute_health(self.loader.skills_dir, skill_name)

    def _resolve_templates(self, content: str, variables: list[str]) -> str | None:
        """Resolve ``{{ var_name }}`` placeholders against OrgMemoryService.

        Imports ADK/``asyncio`` lazily so the default (stdlib-only) path never
        touches them. Returns the resolved content, or ``None`` when resolution
        can't run (missing dependencies / memory not configured), in which case
        the caller falls back to raw content plus the ``template_variables``
        hint.
        """
        try:
            import asyncio

            from nuvel.memory.factory import build_default_service
        except Exception as exc:  # ADK stack not installed
            log(f"OrgMemoryService unavailable (--with-org-memory): {exc}")
            return None

        kwargs = _memory_kwargs(self.with_org_memory)
        try:
            return asyncio.run(
                _resolve_templates_async(content, variables, build_default_service, kwargs)
            )
        except Exception as exc:  # noqa: BLE001 - best-effort resolution
            log(f"OrgMemoryService resolution failed: {exc}")
            return None

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
