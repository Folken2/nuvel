"""Nuvel Skills MCP stdio server (JSON-RPC 2.0).

Exposes a Nuvel Skills hub as MCP resources and tools over stdio. Stdlib only.

Protocol channel: stdout (one JSON message per line).
Logging channel:  stderr.

Resources:
    skill://{theme}/{name}   Full SKILL.md content for a skill.

Tools:
    search_skills        Ranked multi-word search over metadata (and optionally bodies).
    list_skills          Browse skills, optionally by theme, with pagination.
    list_themes          List themes with their skill counts and names.
    get_skill            Fetch a skill's full content + metadata by name.
    install_skill        Plan (default) or apply installing a skill into a skills dir.
    propose_improvement  Send a skill-improvement proposal (GitHub, relay, or local queue).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from nuvel.mcp.installer import install_skill
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

DEFAULT_INSTALL_DIR = "~/.claude/skills"
DEFAULT_FEEDBACK_QUEUE = "~/.nuvel/feedback-queue.jsonl"
RELAY_TIMEOUT_SECONDS = 10

# Search ranking tiers, highest first.
SCORE_EXACT_NAME = 100
SCORE_NAME_PREFIX = 80
SCORE_ALL_IN_NAME = 60
SCORE_ALL_IN_DESCRIPTION = 40
SCORE_METADATA = 20
SCORE_BODY_ONLY = 10

# '-', '_', '.', '/' and whitespace are equivalent word separators in search.
_SEPARATORS = re.compile(r"[\s\-_./]+")


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


def _normalize(text) -> str:
    """Lowercase and collapse separators to single spaces for matching."""
    return _SEPARATORS.sub(" ", str(text or "").lower()).strip()


def _strip_frontmatter(text: str) -> str:
    """Return ``text`` without its leading ``---`` frontmatter block."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[i + 1:])
    return text


def _frontmatter_version(text: str | None) -> str:
    if not text:
        return ""
    return parse_frontmatter(text).get("version", "").strip().strip("\"'")


def _snippet(body: str, tokens: list[str], width: int = 160) -> str:
    """Short single-line excerpt around the first occurrence of any token."""
    lower = body.lower()
    positions = [p for p in (lower.find(t) for t in tokens) if p >= 0]
    if not positions:
        return ""
    pos = min(positions)
    start = max(0, pos - width // 3)
    end = min(len(body), start + width)
    excerpt = " ".join(body[start:end].split())
    return ("…" if start > 0 else "") + excerpt + ("…" if end < len(body) else "")


def _int_arg(args, key, default, maximum=None, minimum=1):
    """Integer argument with a default; values above ``maximum`` are clamped."""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpError(INVALID_PARAMS, f"Argument '{key}' must be an integer")
    if value < minimum:
        raise McpError(INVALID_PARAMS, f"Argument '{key}' must be >= {minimum}")
    return min(value, maximum) if maximum is not None else value


def _bool_arg(args, key, default=False):
    value = args.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise McpError(INVALID_PARAMS, f"Argument '{key}' must be a boolean")
    return value


def _optional_str_arg(args, key):
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise McpError(INVALID_PARAMS, f"Argument '{key}' must be a string")
    return value.strip() or None


def _required_str_arg(args, key):
    value = _optional_str_arg(args, key)
    if value is None:
        raise McpError(INVALID_PARAMS, f"Missing required argument '{key}'")
    return value


def _split_skill_ref(ref: str) -> tuple[str | None, str]:
    """``'theme/name'`` -> (theme, name); ``'name'`` -> (None, name)."""
    if "/" in ref:
        theme, name = ref.split("/", 1)
        return theme, name
    return None, ref


def _feedback_id(proposal: dict) -> str:
    """Stable id: sha256 of the canonical (sorted, compact) proposal JSON."""
    canonical = json.dumps(
        proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _relay_feedback(url: str, payload: dict) -> dict:
    """POST ``payload`` as JSON to the feedback relay; return its JSON reply."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("NUVEL_FEEDBACK_URL must be an http(s) URL")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "nuvel-skills-mcp",
        },
    )
    with urllib.request.urlopen(request, timeout=RELAY_TIMEOUT_SECONDS) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _queue_feedback(record: dict) -> Path:
    """Append ``record`` as one JSON line to the local feedback queue."""
    path = Path(
        os.environ.get("NUVEL_FEEDBACK_QUEUE", "").strip() or DEFAULT_FEEDBACK_QUEUE
    ).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


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
        # When set, discovery (resources/list, search_skills, list_skills,
        # list_themes, get_skill, install_skill) is scoped to this single theme. resources/read is unaffected — scoping
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
            "list_skills": self.tool_list_skills,
            "list_themes": self.tool_list_themes,
            "get_skill": self.tool_get_skill,
            "install_skill": self.tool_install_skill,
            "propose_improvement": self.tool_propose_improvement,
        }

    # --- Scoping helpers -----------------------------------------------------

    def _scoped_entries(self):
        """Yield ``(theme, entry)`` honoring the configured theme scope."""
        for theme, entry in self.loader.iter_entries():
            if self.theme is not None and theme != self.theme:
                continue
            yield theme, entry

    def _entries_for(self, theme: str | None):
        """Scoped entries, further narrowed to ``theme`` when given."""
        for t, entry in self._scoped_entries():
            if theme is not None and t != theme:
                continue
            yield t, entry

    def _resolve_skill(self, ref: str) -> tuple[str, dict]:
        """Resolve ``name`` or ``theme/name`` within the discovery scope."""
        wanted_theme, wanted_name = _split_skill_ref(ref)

        # Under a theme scope, discovery is confined to that theme.
        if self.theme is not None:
            if wanted_theme is not None and wanted_theme != self.theme:
                raise McpError(INVALID_PARAMS, f"Skill not found: {ref}")
            wanted_theme = self.theme

        theme, entry = self.loader.find(wanted_theme, wanted_name)
        if entry is None:
            raise McpError(INVALID_PARAMS, f"Skill not found: {ref}")
        return theme, entry

    def _read_skill_safe(self, theme: str, entry: dict) -> str | None:
        """SKILL.md content, or None when it's missing or unreadable."""
        try:
            return self.loader.read_skill(theme, entry)
        except (SkillsError, OSError, ValueError):
            return None

    def _skill_item(self, theme: str, entry: dict, content: str | None = None) -> dict:
        if content is None:
            content = self._read_skill_safe(theme, entry)
        return {
            "uri": f"skill://{theme}/{entry['name']}",
            "theme": theme,
            "name": entry["name"],
            "description": entry.get("description", ""),
            "author": entry.get("author", ""),
            "version": _frontmatter_version(content),
        }

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
                    "description": (
                        "Find skills relevant to a task. Use this first when you need a "
                        "skill but don't know its exact name. Multi-word queries are "
                        "AND-matched (every word must appear) against skill name, "
                        "description, theme and author; '-', '_', '.' and '/' count as "
                        "spaces, so 'installer transaction' finds "
                        "'installer-transaction-design'. Set include_body=true to also "
                        "search the full SKILL.md text when a metadata search comes back "
                        "empty. Results are ranked best-first; follow up with get_skill."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "One or more words, e.g. 'bug triage' or 'payroll'.",
                            },
                            "theme": {
                                "type": "string",
                                "description": "Only search this theme (see list_themes).",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max results (default 10, max 50).",
                                "default": 10,
                                "minimum": 1,
                                "maximum": 50,
                            },
                            "include_body": {
                                "type": "boolean",
                                "description": "Also search SKILL.md bodies (slower). Default false.",
                                "default": False,
                            },
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "list_skills",
                    "description": (
                        "Browse the skills catalog page by page, optionally within one "
                        "theme. Use this to see what exists when you have no specific "
                        "query; use search_skills when you do. Returns 'total' so you "
                        "can page with limit/offset."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "theme": {
                                "type": "string",
                                "description": "Only list this theme (see list_themes). Unknown themes return no items.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Page size (default 50, max 200).",
                                "default": 50,
                                "minimum": 1,
                                "maximum": 200,
                            },
                            "offset": {
                                "type": "integer",
                                "description": "Number of items to skip (default 0).",
                                "default": 0,
                                "minimum": 0,
                            },
                        },
                    },
                },
                {
                    "name": "list_themes",
                    "description": (
                        "List the catalog's themes (e.g. 'hr', 'sales', "
                        "'software-development') with how many skills each has and "
                        "their names. Use this to orient yourself or to pick a 'theme' "
                        "filter for search_skills / list_skills."
                    ),
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "get_skill",
                    "description": (
                        "Get the full SKILL.md content, frontmatter metadata and version "
                        "for a skill by name or 'theme/name'. Use this to read and "
                        "follow a skill you found via search_skills or list_skills."
                    ),
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
                    "name": "install_skill",
                    "description": (
                        "Install a skill (SKILL.md plus its supporting files) into a local "
                        "skills directory so your harness loads it natively. PLAN FIRST: "
                        "by default (confirm=false) this is read-only and only reports "
                        "the destination, file count and action ('install', 'update' or "
                        "'noop') — nothing is written. Show that plan to the user, then "
                        "call again with confirm=true to actually install. An existing "
                        "copy is backed up to '<dest>.backup' before being replaced; "
                        "symlinked targets are refused."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Skill name (e.g. 'bug-triage') or 'theme/name'.",
                            },
                            "target_dir": {
                                "type": "string",
                                "description": (
                                    "Skills directory to install into; the skill lands in "
                                    "<target_dir>/<name>/. Defaults to "
                                    "$NUVEL_SKILLS_INSTALL_DIR, then ~/.claude/skills."
                                ),
                            },
                            "confirm": {
                                "type": "boolean",
                                "description": "false (default) = plan only; true = write files.",
                                "default": False,
                            },
                        },
                        "required": ["name"],
                    },
                },
                {
                    "name": "propose_improvement",
                    "description": (
                        "Propose an improvement to a skill after using it and finding "
                        "it drifted (outdated tooling, missing edge cases, wrong "
                        "assumptions). The proposal is filed as a GitHub issue, sent to "
                        "the configured feedback relay, or saved to a local queue; the "
                        "'delivery' field says which, and 'feedback_id' identifies it."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Name of the skill being improved (e.g. 'bug-triage') or 'theme/name'.",
                            },
                            "current_version": {
                                "type": "string",
                                "description": "Optional. Version you used; read from the SKILL.md frontmatter when omitted.",
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
                        "required": ["skill_name", "issue", "suggested_fix"],
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
        query = _required_str_arg(args, "query")
        normalized_query = _normalize(query)
        tokens = normalized_query.split()
        if not tokens:
            raise McpError(INVALID_PARAMS, "Argument 'query' must contain a word")
        theme = _optional_str_arg(args, "theme")
        limit = _int_arg(args, "limit", 10, maximum=50)
        include_body = _bool_arg(args, "include_body")

        hits = []
        for t, entry in self._entries_for(theme):
            fields = {
                "name": _normalize(entry.get("name", "")),
                "description": _normalize(entry.get("description", "")),
                "theme": _normalize(t),
                "author": _normalize(entry.get("author", "")),
            }
            content = body = None
            if include_body:
                content = self._read_skill_safe(t, entry)
                if content:
                    body = _strip_frontmatter(content)
                    fields["body"] = _normalize(body)

            if not all(any(tok in v for v in fields.values()) for tok in tokens):
                continue
            matched_in = [
                f for f, v in fields.items() if any(tok in v for tok in tokens)
            ]
            name = fields["name"]
            if name == normalized_query:
                score = SCORE_EXACT_NAME
            elif name.startswith(normalized_query):
                score = SCORE_NAME_PREFIX
            elif all(tok in name for tok in tokens):
                score = SCORE_ALL_IN_NAME
            elif all(tok in fields["description"] for tok in tokens):
                score = SCORE_ALL_IN_DESCRIPTION
            elif all(
                any(tok in v for f, v in fields.items() if f != "body")
                for tok in tokens
            ):
                score = SCORE_METADATA
            else:
                score = SCORE_BODY_ONLY
            snippet = _snippet(body, tokens) if "body" in matched_in else ""
            hits.append((score, str(entry.get("name", "")).lower(), t, entry,
                         content, matched_in, snippet))

        hits.sort(key=lambda h: (-h[0], h[1], h[2]))
        results = []
        for score, _, t, entry, content, matched_in, snippet in hits[:limit]:
            item = self._skill_item(t, entry, content)
            item.update(score=score, matched_in=matched_in, snippet=snippet)
            results.append(item)
        return {
            "query": query,
            "count": len(results),
            "total": len(hits),
            "results": results,
        }

    def tool_list_skills(self, args):
        theme = _optional_str_arg(args, "theme")
        limit = _int_arg(args, "limit", 50, maximum=200)
        offset = _int_arg(args, "offset", 0, minimum=0)
        entries = sorted(
            self._entries_for(theme),
            key=lambda te: (te[0], str(te[1].get("name", "")).lower()),
        )
        items = [self._skill_item(t, e) for t, e in entries[offset:offset + limit]]
        return {
            "total": len(entries),
            "count": len(items),
            "offset": offset,
            "items": items,
        }

    def tool_list_themes(self, args):
        grouped: dict[str, list[str]] = {}
        for t, entry in self._scoped_entries():
            grouped.setdefault(t, []).append(entry["name"])
        themes = [
            {"theme": t, "count": len(names), "skills": sorted(names)}
            for t, names in sorted(grouped.items())
        ]
        return {"count": len(themes), "themes": themes}

    def tool_get_skill(self, args):
        name = _required_str_arg(args, "name")
        theme, entry = self._resolve_skill(name)

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
            "version": _frontmatter_version(content),
            "metadata": parse_frontmatter(content),
            "content": content,
        }

    def tool_install_skill(self, args):
        name = _required_str_arg(args, "name")
        target_dir = (
            _optional_str_arg(args, "target_dir")
            or os.environ.get("NUVEL_SKILLS_INSTALL_DIR", "").strip()
            or DEFAULT_INSTALL_DIR
        )
        confirm = _bool_arg(args, "confirm")
        theme, entry = self._resolve_skill(name)

        skill_name = entry["name"]
        try:
            content = self.loader.read_skill(theme, entry)
        except SkillsError as exc:
            return {
                "status": "blocked",
                "action": None,
                "skill": skill_name,
                "target": str(Path(target_dir).expanduser() / skill_name),
                "target_dir": str(Path(target_dir).expanduser()),
                "files": 0,
                "paths": [],
                "backup": None,
                "error": f"{exc} (not vendored in this hub; install it from its upstream source)",
            }
        source_dir = self.loader.skill_path(theme, entry).parent
        result = install_skill(skill_name, source_dir, content, target_dir, confirm)
        result["uri"] = f"skill://{theme}/{skill_name}"
        result["version"] = _frontmatter_version(content)
        return result

    def _lookup_version(self, skill_ref: str) -> str:
        """Frontmatter version for ``name`` / ``theme/name`` ('' if unknown)."""
        theme, entry = self.loader.find(*_split_skill_ref(skill_ref))
        if entry is None:
            return ""
        return _frontmatter_version(self._read_skill_safe(theme, entry))

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

        if not current_version:
            current_version = self._lookup_version(skill_name) or "unknown"

        title, body = _improvement_issue(
            skill_name, current_version, issue, suggested_fix, harness
        )
        proposal = {
            "skill_name": skill_name,
            "current_version": current_version,
            "issue": issue,
            "suggested_fix": suggested_fix,
            "harness": harness,
        }
        feedback_id = _feedback_id(proposal)

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return self._deliver_without_token(proposal, feedback_id, title, body)

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
            "delivery": "github",
            "message": "Improvement proposal filed as GitHub issue",
            "feedback_id": feedback_id,
            "current_version": current_version,
            "issue_url": data.get("html_url"),
            "issue_number": data.get("number"),
            "title": title,
        }

    def _deliver_without_token(self, proposal, feedback_id, title, body):
        """Relay to NUVEL_FEEDBACK_URL if set, else (or on failure) queue locally."""
        record = dict(proposal, feedback_id=feedback_id, title=title, body=body)
        relay_url = os.environ.get("NUVEL_FEEDBACK_URL", "").strip()
        relay_error = None
        if relay_url:
            try:
                data = _relay_feedback(relay_url, record)
            except (OSError, ValueError) as exc:  # URLError/HTTPError are OSErrors
                relay_error = str(exc)
                log(f"Feedback relay failed, queueing locally: {exc}")
            else:
                return {
                    "status": "relayed",
                    "delivery": "relay",
                    "message": "Improvement proposal sent to the feedback relay",
                    "feedback_id": feedback_id,
                    "current_version": proposal["current_version"],
                    "issue_url": data.get("issue_url"),
                    "id": data.get("id"),
                    "title": title,
                }

        record["queued_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if relay_error:
            record["relay_error"] = relay_error
        try:
            queue_path = _queue_feedback(record)
        except OSError as exc:
            raise McpError(
                INTERNAL_ERROR, f"Could not deliver or queue the proposal: {exc}"
            )
        result = {
            "status": "queued",
            "delivery": "queue",
            "message": (
                "No GITHUB_TOKEN or reachable NUVEL_FEEDBACK_URL — proposal saved "
                "to the local feedback queue for later delivery"
            ),
            "feedback_id": feedback_id,
            "current_version": proposal["current_version"],
            "queue_path": str(queue_path),
            "title": title,
        }
        if relay_error:
            result["relay_error"] = relay_error
        return result

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
