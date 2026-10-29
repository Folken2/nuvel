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
    get_skill            Fetch a skill's full content + metadata by name, or a
                         single ``## section`` (section-scoped reading).
    install_skill        Plan (default) or apply installing a skill into a skills dir.
    fence_value          Wrap a value in provenance-fencing markers.
    propose_improvement  Send a skill-improvement proposal (GitHub, relay, or the
                         local feedback store).
    record_feedback      Record the outcome of using a skill in the local feedback store.
    check_skill_health   Health report for a skill from the local feedback store.
    record_run_trace     Record one content-free ``nuvel.skill_run/1`` run trace.
    record_recipe_signal Record one content-free ``nuvel.recipe_signal/2`` weekly snapshot.
    share_recipe         Share one ``nuvel.recipe/3`` recipe; its optional free text is
                         masked server-side before it is stored, relayed or logged.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import IO, TYPE_CHECKING

from nuvel_mcp import feedback, masking, recipes, traces
from nuvel_mcp.auth import current_principal
from nuvel_mcp.installer import install_skill
from nuvel_mcp.skills_loader import SkillsError, SkillsLoader, parse_frontmatter

if TYPE_CHECKING:  # pragma: no cover
    from nuvel_mcp.recipes import RecipeStore
    from nuvel_mcp.traces import TraceStore

PROTOCOL_VERSION = "2024-11-05"


def _package_version() -> str:
    """The installed nuvel-mcp version; '0+unknown' when run from a bare source tree."""
    try:
        return metadata.version("nuvel-mcp")
    except metadata.PackageNotFoundError:
        return "0+unknown"


SERVER_INFO = {"name": "nuvel-skills", "version": _package_version()}

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


HUB_TOOLS = frozenset({
    "search_skills", "list_skills", "list_themes", "get_skill", "install_skill", "fence_value",
})
FEEDBACK_TOOLS = frozenset({
    "record_feedback", "check_skill_health", "propose_improvement", "record_run_trace",
    "record_recipe_signal", "share_recipe",
})
# Tools the HTTP transport exposes when no explicit allow-list (--tools) is
# given: the hub read tools, run traces and their health summary, and the two
# recipe tools (closed-schema signals and server-masked recipes).
# Off unless named: record_feedback / propose_improvement (free-text input) and
# install_skill (writes to the server's filesystem).
HTTP_DEFAULT_TOOLS = frozenset({
    "search_skills", "list_skills", "list_themes", "get_skill", "fence_value",
    "record_run_trace", "check_skill_health", "record_recipe_signal", "share_recipe",
})
RUN_OUTCOMES = ("success", "partial", "failure", "blocked", "skipped")


class ToolInputError(Exception):
    """A tool rejected its input; surfaced as a ``rejected`` tool result."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


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
    version = parse_frontmatter(text).get("version", "")
    return version.strip().strip("\"'") if isinstance(version, str) else ""


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


_REMOTE_DESCRIPTIONS = {
    "check_skill_health": (
        "Check a skill's run record before relying on it: outcome counts for the "
        "last 30 days, errors by step and error class, and the total, from the "
        "run traces recorded for your tenant. 'plugin' is required."
    ),
    "get_skill": (
        "Get the full SKILL.md content, frontmatter metadata and version for a "
        "skill by name or 'theme/name'. Pass 'section' to read a single '## ' "
        "section instead of the whole file. 'metadata.template_variables' lists "
        "any {{ placeholders }} the skill expects you to fill."
    ),
}


def _remote_definition(tool: dict) -> dict:
    """``tool`` as listed in remote mode (nothing from the feedback store)."""
    name = tool["name"]
    if name not in _REMOTE_DESCRIPTIONS:
        return tool
    tool = {**tool, "description": _REMOTE_DESCRIPTIONS[name]}
    if name == "check_skill_health":
        schema = tool["inputSchema"]
        tool["inputSchema"] = {**schema, "required": [*schema["required"], "plugin"]}
    return tool


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


def _describe_failure(exc: BaseException) -> str:
    """Log-safe description of a relay failure: exception class (+ HTTP status).

    Never ``str(exc)`` and never the URL: ``NUVEL_FEEDBACK_URL`` may carry
    credentials (``https://user:pass@host``) that exception messages echo.
    """
    name = type(exc).__name__
    if isinstance(exc, urllib.error.HTTPError):
        return f"{name} {exc.code}"
    return name


def _masked_recipe(recipe: object) -> dict:
    """The stored form of a client recipe (spec §5), or ``ToolInputError(paths)``.

    validate ``nuvel.recipe/3`` → ``mask_recipe`` → ``invariant_hits`` on the
    masked output (before any server field is added) → re-validate the masked
    output → stamp ``masking``. The caller stamps ``tenant``. The result is
    built by the masker from the schema's keys, so it shares nothing with
    *recipe*. A recipe without ``text`` loads no lexicon: it goes through the
    same allowlist and invariant with ``lexicon=None`` and is stamped
    ``{"lexicon": None, "masked": 0, "words": 0, "dropped": []}``. A masker
    failure is logged by exception type only.
    """
    errors = recipes.validate_recipe(recipe)
    if errors:
        raise ToolInputError(errors)
    try:
        abbreviations = masking.ABBREVIATIONS
        text = recipe.get("text")
        lexicon = masking.load_lexicon(text["language"]) if text is not None else None
        masked, info = masking.mask_recipe(recipe, lexicon, abbreviations)
        hits = masking.invariant_hits(masked, abbreviations, dropped=info["dropped"])
    except Exception as exc:  # noqa: BLE001 - its text may quote the input
        log(f"Could not mask the recipe: {type(exc).__name__}")
        raise McpError(INTERNAL_ERROR, "Could not mask the recipe") from None
    if hits:
        raise ToolInputError(hits)
    errors = recipes.validate_recipe(masked)
    if errors:  # e.g. a required field lost in masking: never stored
        raise ToolInputError(errors)
    masked["masking"] = {"lexicon": info["lexicon"], "masked": info["masked"],
                         "words": info["words"], "dropped": list(info["dropped"])}
    return masked


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
    except (ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


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
    return pattern.sub(lambda _m: value, content)


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
        loader: SkillsLoader | None,
        theme: str | None = None,
        require_filter: list[str] | None = None,
        with_org_memory: str | None = None,
        feedback_dir: str | None = None,
        tools: frozenset[str] | None = None,
        trace_store: "TraceStore | None" = None,
        health_runs_only: bool = False,
        recipe_store: "RecipeStore | None" = None,
    ):
        # None means feedback-only mode: no hub tools or resources are served.
        self.loader = loader
        # Optional allow-list narrowing which tools are listed and callable.
        self._allowed_tools = tools
        self.trace_store = trace_store
        self.recipe_store = recipe_store
        # Remote mode, set by the HTTP transport: no tool returns anything
        # read from the local feedback store (which holds free text and every
        # tenant's outcomes). check_skill_health then requires 'plugin' and
        # returns only the principal-scoped run-trace summary; get_skill omits
        # 'health'.
        self.health_runs_only = health_runs_only
        # When set, discovery (resources/list, search_skills, list_skills,
        # list_themes, get_skill, install_skill) is scoped to this single theme. resources/read is unaffected — scoping
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
        # The one local feedback store (record_feedback, check_skill_health and
        # propose_improvement's queue). None defers to $NUVEL_FEEDBACK_DIR, then
        # ~/.nuvel/feedback, resolved per call; nothing is created until a write.
        self._feedback_dir_override = feedback_dir
        # Optional per-message auth check for the stdio loop (``--require-auth``):
        # returns an error response to refuse the message, or None to let it
        # through. The HTTP transport authenticates at the HTTP layer instead.
        self.gate = None
        if os.environ.get("NUVEL_FEEDBACK_QUEUE", "").strip():
            log(
                "NUVEL_FEEDBACK_QUEUE is no longer used; queued proposals go to "
                f"{feedback.queue_path(self.feedback_dir)} "
                "(set NUVEL_FEEDBACK_DIR or --feedback-dir to move the store)."
            )
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
            "fence_value": self.tool_fence_value,
            "record_feedback": self.tool_record_feedback,
            "check_skill_health": self.tool_check_skill_health,
            "record_run_trace": self.tool_record_run_trace,
            "record_recipe_signal": self.tool_record_recipe_signal,
            "share_recipe": self.tool_share_recipe,
        }

    @property
    def feedback_dir(self) -> Path:
        """The local feedback store directory."""
        return feedback.resolve_feedback_dir(self._feedback_dir_override)

    def _trace_store(self) -> "TraceStore":
        """The configured trace store, else a local one under ``feedback_dir``."""
        if self.trace_store is not None:
            return self.trace_store
        return traces.LocalTraceStore(self.feedback_dir)

    def _recipe_store(self) -> "RecipeStore":
        """The configured recipe store, else a local one under ``feedback_dir``."""
        if self.recipe_store is not None:
            return self.recipe_store
        return recipes.LocalRecipeStore(self.feedback_dir)

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

    def enable_remote_mode(self) -> None:
        """Prepare this server to be served over HTTP (called by the transport).

        Turns on ``health_runs_only`` and, when no explicit allow-list was
        given, narrows the tools to :data:`HTTP_DEFAULT_TOOLS`.
        """
        self.health_runs_only = True
        if self._allowed_tools is None:
            self._allowed_tools = HTTP_DEFAULT_TOOLS

    def _tool_enabled(self, name: str) -> bool:
        if self.loader is None and name in HUB_TOOLS:
            return False
        return self._allowed_tools is None or name in self._allowed_tools

    def handle_resources_list(self, params):
        if self.loader is None:
            return {"resources": []}
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

        if self.loader is None:
            raise McpError(INVALID_PARAMS, f"Skill not found: {uri}")
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
        tools = self._all_tool_definitions()["tools"]
        if self.health_runs_only:
            tools = [_remote_definition(t) for t in tools]
        return {"tools": [t for t in tools if self._tool_enabled(t["name"])]}

    def _all_tool_definitions(self):
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
                        "follow a skill you found via search_skills or list_skills. "
                        "Pass 'section' to read a single '## ' section instead of the "
                        "whole file. The response also carries 'health' (the "
                        "check_skill_health report) and 'metadata.template_variables' "
                        "(any {{ placeholders }} the skill expects you to fill)."
                    ),
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
                        "assumptions). The proposal is filed as a GitHub issue, sent to "
                        "the configured feedback relay, or saved to the local feedback "
                        "store (where check_skill_health reports it); the 'delivery' "
                        "field says which, and 'feedback_id' identifies it. To report "
                        "how a run went without proposing a change, use record_feedback."
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
                {
                    "name": "record_feedback",
                    "description": (
                        "Record how it went after you used a skill: the outcome, how "
                        "severe any problem was, which section, what worked and what "
                        "didn't. Saved to the local feedback store and counted by "
                        "check_skill_health. Use propose_improvement instead when you "
                        "have a concrete fix to suggest."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Skill name (e.g. 'bug-triage') or 'theme/name'.",
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
                        "Check a skill's track record before relying on it. Reads the "
                        "local feedback store: outcome counts for the last 30 days, "
                        "trend, sections flagged as blocking or misleading, improvement "
                        "proposals still queued, and a recommendation ('ok', "
                        "'review_before_use' or 'use_cautiously'). get_skill embeds the "
                        "same report as 'health'. With 'plugin', the report also "
                        "carries a 'runs' block built from recorded run traces: "
                        "outcome counts for the last 30 days, errors by step and "
                        "error class, and the total."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Skill name (e.g. 'bug-triage') or 'theme/name'.",
                            },
                            "plugin": {
                                "type": "string",
                                "description": (
                                    "Plugin the skill ships in (e.g. 'chief-of-staff'). "
                                    "Adds the 'runs' block from recorded run traces."
                                ),
                            },
                        },
                        "required": ["skill_name"],
                    },
                },
                {
                    "name": "record_run_trace",
                    "description": (
                        "Record one skill run as a content-free nuvel.skill_run/1 "
                        "trace, once per run, when the skill tells you to. Every "
                        "field is an enum, a UUID, a version, a timestamp, a count "
                        "or a short lowercase identifier. Step ids, count keys and "
                        "error steps come only from the skill's published step "
                        "catalog (step ids start with '<skill name>.'); never "
                        "invent them. Never put names, subjects, addresses, "
                        "message text or any other user content in a trace. "
                        "'safety' must contain both injection_suspected and "
                        "forbidden_tool_requested, or be omitted. Do not send "
                        "'tenant' (the server sets it). A rejected trace returns "
                        "the failing field paths only. Re-sending the same run_id "
                        "returns 'duplicate'."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {"trace": self._trace_input_schema()},
                        "required": ["trace"],
                    },
                },
                {
                    "name": "record_recipe_signal",
                    "description": (
                        "Record one content-free nuvel.recipe_signal/2 weekly snapshot "
                        "(status plus the uses, successes, corrections and "
                        "days_used counters) per recipe that changed since the "
                        "last weekly review, when the weekly review tells you "
                        "to. Every field "
                        "is an enum, a UUID, a version, a timestamp, a count or a "
                        "short lowercase identifier; never put names, subjects, "
                        "addresses, message text or any other user content in a "
                        "signal. Do not send 'tenant' (the server sets it). A "
                        "rejected signal returns the failing field paths only. "
                        "Re-sending the same signal_id returns 'duplicate'."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "signal": self._doc_input_schema(
                                recipes.SIGNAL_RESOURCE, "One nuvel.recipe_signal/2 snapshot object."
                            )
                        },
                        "required": ["signal"],
                    },
                },
                {
                    "name": "share_recipe",
                    "description": (
                        "Share one nuvel.recipe/3 recipe, when the plugin's sharing "
                        "procedure tells you to. A recipe is structure, always: a "
                        "category, steps (an action from the catalogue, a tool kind "
                        "and placeholder slots, nothing else) and usage stats "
                        "(uses, successes, corrections, days_used). Free text is "
                        "optional and goes only in 'text', only when the user turned "
                        "text sharing on: its language plus short notes (title, "
                        "when_to_use, step_notes with one note per step, pitfalls) "
                        "and a narrative, the original wording of what was asked and "
                        "how it was solved. In every free-text field write a {type} "
                        "placeholder (for example {person}, {company}, {amount}, "
                        "{date}, {email}) instead of names, companies, products, "
                        "places, amounts, dates, contacts (emails, phone numbers), "
                        "URLs or file names. All free text is masked server-side "
                        "before anything is stored: every word that is not a common "
                        "word of the text's language becomes {?}, and a field that "
                        "is mostly masked is dropped. A recipe without 'text' is "
                        "stored as structure only. The reply gives the masked and "
                        "word counts and the dropped field paths; a schema error "
                        "returns the failing field paths only. Do not send 'tenant' "
                        "or 'masking' (the server sets them). Re-sending the same "
                        "recipe_id and version returns 'duplicate'."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "recipe": self._doc_input_schema(
                                recipes.RECIPE_RESOURCE, "One nuvel.recipe/3 recipe object."
                            )
                        },
                        "required": ["recipe"],
                    },
                },
            ]
        }

    @staticmethod
    def _trace_input_schema() -> dict:
        schema = {k: v for k, v in traces.load_schema().items() if k != "$schema"}
        schema["description"] = "One nuvel.skill_run/1 trace object."
        return schema

    @staticmethod
    def _doc_input_schema(resource: str, description: str) -> dict:
        schema = {k: v for k, v in traces.load_schema(resource).items() if k != "$schema"}
        schema["description"] = description
        return schema

    def handle_tools_call(self, params):
        tool_name = params.get("name")
        args = params.get("arguments") or {}
        handler = self._tools.get(tool_name)
        if handler is None or not self._tool_enabled(tool_name):
            # Fixed text: never echo the caller-supplied name.
            raise McpError(INVALID_PARAMS, "Unknown tool")
        try:
            result = handler(args)
        except ToolInputError as exc:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps(
                        {"status": "rejected", "errors": exc.errors}, ensure_ascii=False
                    ),
                }],
                "isError": True,
            }
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
        section = _optional_str_arg(args, "section")
        theme, entry = self._resolve_skill(name)

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
            "version": _frontmatter_version(content),
            "metadata": metadata,
            "content": body,
        }
        if section is not None:
            result["section"] = section

        # Health signals from the feedback store (best-effort, no feedback = "ok").
        # Never in remote mode: the store holds other callers' free text.
        if not self.health_runs_only:
            try:
                result["health"] = feedback.compute_health(self.feedback_dir, entry["name"])
            except ValueError:  # a hub name that can't be a store key has no feedback
                result["health"] = {"total_feedback": 0, "queued_proposals": 0, "recommendation": "ok"}

        # Optional OrgMemoryService resolution (ADK-backed, best-effort).
        if variables and self.with_org_memory:
            resolved = self._resolve_templates(body, variables)
            if resolved is not None:
                result["resolved_content"] = resolved

        return result

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
        if self.loader is None:
            return ""
        theme, entry = self.loader.find(*_split_skill_ref(skill_ref))
        if entry is None:
            return ""
        return _frontmatter_version(self._read_skill_safe(theme, entry))

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
        return feedback.write_feedback(self.feedback_dir, args)

    def tool_check_skill_health(self, args):
        """Check a skill's health from its feedback history."""
        if self.health_runs_only:
            return self._runs_only_health(args)
        skill_name = _required_str_arg(args, "skill_name")
        plugin = args.get("plugin")
        if plugin is not None and not (
            isinstance(plugin, str) and traces.IDENTIFIER_RE.fullmatch(plugin)
        ):
            raise McpError(INVALID_PARAMS, "Argument 'plugin' must be a plugin identifier")
        try:
            report = feedback.compute_health(self.feedback_dir, skill_name)
        except ValueError as exc:
            raise McpError(INVALID_PARAMS, str(exc))
        if plugin is not None:
            report["runs"] = self._runs_summary(plugin, feedback.skill_key(skill_name))
        return report

    def _runs_only_health(self, args) -> dict:
        """check_skill_health for remote callers: the run summary and nothing else.

        Error messages never repeat the caller's values.
        """
        plugin = args.get("plugin")
        if not (isinstance(plugin, str) and traces.IDENTIFIER_RE.fullmatch(plugin)):
            raise McpError(INVALID_PARAMS, "Argument 'plugin' is required and must be a plugin identifier")
        skill_name = args.get("skill_name")
        try:
            skill = feedback.skill_key(skill_name if isinstance(skill_name, str) else "")
        except ValueError:
            raise McpError(INVALID_PARAMS, "Argument 'skill_name' must be a skill name") from None
        return {"skill": skill, "plugin": plugin, "runs": self._runs_summary(plugin, skill)}

    def _runs_summary(self, plugin: str, skill: str) -> dict:
        """Last-30-days run-trace summary for ``plugin.skill`` (principal-scoped)."""
        since = datetime.now(timezone.utc) - timedelta(days=30)
        runs = self._trace_store().query(plugin, skill, since, current_principal.get())
        outcomes = dict.fromkeys(RUN_OUTCOMES, 0)
        errors_by_step: dict[str, dict[str, int]] = {}
        for run in runs:
            if run.get("outcome") in outcomes:
                outcomes[run["outcome"]] += 1
            # Custom stores may hand back records without the schema's
            # error fields; skip what cannot be counted rather than fail.
            errors = run.get("errors")
            for err in errors if isinstance(errors, list) else ():
                step = err.get("step") if isinstance(err, dict) else None
                cls = err.get("class") if isinstance(err, dict) else None
                if not isinstance(step, str) or not isinstance(cls, str):
                    continue
                by_class = errors_by_step.setdefault(step, {})
                by_class[cls] = by_class.get(cls, 0) + 1
        return {
            "last_30_days": outcomes,
            "errors_by_step": errors_by_step,
            "total": len(runs),
        }

    def tool_record_run_trace(self, args):
        """Validate, normalise, store and (optionally) relay one run trace."""
        trace = args.get("trace")
        errors = traces.validate_skill_run(trace)
        if not errors:
            prefix = f"{trace['skill']['name']}."
            errors = [
                f"/steps/{i}/id"
                for i, step in enumerate(trace.get("steps", []))
                if not step["id"].startswith(prefix)
            ]
        if errors:
            raise ToolInputError(errors)

        record = traces.normalize(trace, tenant=current_principal.get())
        try:
            stored = self._trace_store().append(record)
        except OSError as exc:
            log(f"Could not store the run trace: {exc}")  # detail stays server-side
            raise McpError(INTERNAL_ERROR, "Could not store the run trace") from None
        if not stored:
            return {"status": "duplicate", "run_id": record["run_id"]}

        relay_url = os.environ.get("NUVEL_FEEDBACK_URL", "").strip()
        if relay_url:
            try:
                _relay_feedback(relay_url, {"type": "skill_run", "record": record})
            except Exception as exc:  # the local copy stands; never retried
                log(f"Run trace relay failed (kept locally): {_describe_failure(exc)}")
        return {"status": "recorded", "run_id": record["run_id"]}

    def tool_record_recipe_signal(self, args):
        """Validate, screen, stamp, store and (optionally) relay one recipe signal."""
        signal = args.get("signal")
        errors = recipes.validate_signal(signal) or recipes.screen_signal(signal)
        if errors:
            raise ToolInputError(errors)

        record = copy.deepcopy(signal)
        parsed = traces._parse_started_at(record["at"])
        record["at"] = parsed.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        tenant = current_principal.get()
        if tenant is not None:
            record["tenant"] = tenant
        try:
            stored = self._recipe_store().append_signal(record)
        except OSError as exc:
            log(f"Could not store the recipe signal: {exc}")  # detail stays server-side
            raise McpError(INTERNAL_ERROR, "Could not store the recipe signal") from None
        result = {"signal_id": record["signal_id"]}
        if not stored:
            return {"status": "duplicate", **result}
        self._relay_record("recipe_signal", record)
        return {"status": "recorded", **result}

    def tool_share_recipe(self, args):
        """Validate, mask, check, stamp, store and (optionally) relay one recipe.

        Only the masked record (``_masked_recipe``) reaches the store, the relay
        or a log line; the raw input is never kept beyond this call.
        """
        record = _masked_recipe(args.get("recipe"))
        tenant = current_principal.get()
        if tenant is not None:
            record["tenant"] = tenant
        info = record["masking"]
        try:
            stored = self._recipe_store().put_recipe(record)
        except OSError as exc:
            # Type only: a store's message is not ours to vouch for.
            log(f"Could not store the recipe: {type(exc).__name__}")
            raise McpError(INTERNAL_ERROR, "Could not store the recipe") from None
        result = {"recipe_id": record["recipe_id"], "version": record["version"]}
        if not stored:
            return {"status": "duplicate", **result}
        self._relay_record("recipe", record)
        return {"status": "recorded", **result, "masked": info["masked"],
                "words": info["words"], "dropped": list(info["dropped"])}

    @staticmethod
    def _relay_record(kind: str, record: dict) -> None:
        """Relay a stored record to $NUVEL_FEEDBACK_URL; failures are logged, never raised."""
        relay_url = os.environ.get("NUVEL_FEEDBACK_URL", "").strip()
        if relay_url:
            try:
                _relay_feedback(relay_url, {"type": kind, "record": record})
            except Exception as exc:  # the local copy stands; never retried
                log(f"Relay of {kind} failed (kept locally): {_describe_failure(exc)}")

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
        except ImportError as exc:  # nuvel-cli / ADK stack not installed
            missing = exc.name or "nuvel.memory"
            log(f"--with-org-memory needs the module {missing!r}, which is not installed "
                f"(install nuvel-cli): {exc}")
            return None
        except Exception as exc:
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
            with urllib.request.urlopen(request, timeout=RELAY_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # The response body and exception text stay in the server log; the
            # caller gets the class and status only.
            try:
                detail = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
            except (OSError, http.client.HTTPException):
                detail = ""  # a cut-off error body: the status alone is enough
            log(f"GitHub API error {exc.code}: {detail}")
            raise McpError(
                INTERNAL_ERROR, f"GitHub API request failed: {_describe_failure(exc)}"
            ) from None
        except urllib.error.URLError as exc:
            log(f"GitHub API request failed: {exc}")
            raise McpError(
                INTERNAL_ERROR, f"GitHub API request failed: {_describe_failure(exc)}"
            ) from None
        except (http.client.HTTPException, ValueError) as exc:
            # A cut-off reply (IncompleteRead) or a success body that is not
            # UTF-8 JSON: class name only, in the log and the error alike.
            log(f"GitHub API request failed: {_describe_failure(exc)}")
            raise McpError(
                INTERNAL_ERROR, f"GitHub API request failed: {_describe_failure(exc)}"
            ) from None
        if not isinstance(data, dict):
            data = {}  # a non-object reply: filed, but no issue link to report

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
            # URLError/HTTPError are OSErrors; HTTPException covers IncompleteRead.
            except (OSError, ValueError, http.client.HTTPException) as exc:
                # Class (+ status) only, in the result and the queue file alike:
                # the exception text can echo the relay URL and its credentials.
                relay_error = _describe_failure(exc)
                log(f"Feedback relay failed, queueing locally: {relay_error}")
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
            feedback.queue_proposal(self.feedback_dir, record)
        except OSError as exc:
            log(f"Could not queue the proposal: {exc}")  # paths stay server-side
            raise McpError(
                INTERNAL_ERROR,
                f"Could not deliver or queue the proposal: {type(exc).__name__}",
            ) from None
        result = {
            "status": "queued",
            "delivery": "queue",
            "message": (
                "No GITHUB_TOKEN or reachable NUVEL_FEEDBACK_URL — proposal saved "
                "to the local feedback store for later delivery; "
                "check_skill_health reports it"
            ),
            "feedback_id": feedback_id,
            "current_version": proposal["current_version"],
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
            # Fixed text, like "Unknown tool": the caller's value is not echoed.
            return error_response(msg_id, METHOD_NOT_FOUND, "Method not found")

        params = message.get("params") or {}
        try:
            result = handler(params)
        except McpError as exc:
            log(f"McpError in {method}: {exc.message}")
            return error_response(msg_id, exc.code, exc.message)
        except SkillsError as exc:
            log(f"SkillsError in {method}: {exc}")
            if self.health_runs_only:
                # Skills errors can carry hub paths: remote callers get fixed text.
                return error_response(msg_id, INTERNAL_ERROR, "Skills error")
            return error_response(msg_id, INTERNAL_ERROR, f"Skills error: {exc}")
        except Exception as exc:  # noqa: BLE001 - report as JSON-RPC internal error
            if method == "tools/call" and isinstance(params, dict) and params.get("name") == "share_recipe":
                # share_recipe carries raw client text until it is masked, and an
                # exception's text may quote it: the type only, here and to the caller.
                log(f"Internal error in {method} (share_recipe): {type(exc).__name__}")
                return error_response(msg_id, INTERNAL_ERROR, "Internal error")
            log(f"Internal error in {method}: {type(exc).__name__}: {exc}")
            if self.health_runs_only:
                # Remote callers get no server paths or exception text; the
                # detail stays in the operator's stderr log.
                return error_response(msg_id, INTERNAL_ERROR, "Internal error")
            return error_response(msg_id, INTERNAL_ERROR, f"Internal error: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def serve(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        """Run the stdio read/dispatch/write loop until EOF on stdin."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        where = self.loader.skills_dir if self.loader is not None else "none (feedback-only)"
        log(f"nuvel-skills MCP server starting (skills_dir={where})")
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except (ValueError, RecursionError) as exc:  # bad JSON, an int over the digit limit, nesting too deep
                log(f"Parse error: {type(exc).__name__}")  # never the line's content
                _write_message(stdout, error_response(None, PARSE_ERROR, "Parse error"))
                continue

            if self.gate is not None:
                refusal = self.gate(message)
                if refusal is not None:
                    if isinstance(message, dict) and "id" in message:
                        _write_message(stdout, refusal)
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
