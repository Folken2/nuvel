"""Post-run skill curator — Hermes-Agent-inspired self-improving skills loop.

Runs at *Runner scope* as an ADK plugin. Observes the FULL trajectory of a
run (tool calls, events, errors across every sub-agent) and, when the run is
"complex enough", asks the agent's own LLM whether the run reveals either
(a) a new reusable pattern worth becoming a skill, or
(b) a missing edge case in an existing skill.

Safety boundaries (preserved from the previous after_agent_callback):

* **Off by default.** Only runs when ``NUVEL_SKILL_CURATOR=1``.
* **Never auto-applies.** Proposals land in
  ``~/.nuvel/skill-proposals/<timestamp>-<name>.md`` (override with
  ``NUVEL_SKILL_PROPOSALS_DIR``) — outside the project tree by default.
* **No new third-party deps.** ``llm_fn`` is injectable for tests; the
  production wiring uses ``google.genai`` already pulled in by ADK.

Design notes:

* Counters live on the plugin instance and are reset in
  ``before_run_callback`` so each run has a clean slate.
* ``after_tool_callback`` / ``on_event_callback`` / ``on_tool_error_callback``
  accumulate signal during the run.
* ``after_run_callback`` evaluates the heuristic, builds the prompt, and
  dispatches to the LLM exactly once per run.
* Multi-agent attribution: every event author is captured so the proposal
  frontmatter can list ``triggering_agents`` instead of just one agent.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.events.event import Event
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# Environment knobs.
ENV_ENABLED = "NUVEL_SKILL_CURATOR"
ENV_MIN_TOOLS = "NUVEL_SKILL_CURATOR_MIN_TOOLS"
ENV_MIN_EVENTS = "NUVEL_SKILL_CURATOR_MIN_EVENTS"
ENV_MIN_ERRORS = "NUVEL_SKILL_CURATOR_MIN_ERRORS"
ENV_PROPOSALS_DIR = "NUVEL_SKILL_PROPOSALS_DIR"
ENV_SKILLS_DIR = "NUVEL_SKILLS_DIR"
ENV_MODEL = "NUVEL_SKILL_CURATOR_MODEL"

DEFAULT_MIN_TOOLS = 5
DEFAULT_MIN_EVENTS = 12
DEFAULT_MIN_ERRORS = 3
DEFAULT_MODEL = "gemini-2.0-flash"
VALID_ACTIONS = {"noop", "propose_new", "patch_existing"}

LlmFn = Callable[[str], str]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── Module-level helpers (mirror the callback's API for test parity) ──


def _enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _existing_skill_names(skills_dir: Path) -> list[str]:
    if not skills_dir.is_dir():
        return []
    out: list[str] = []
    for sub in sorted(skills_dir.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            out.append(sub.name)
    return out


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "skill").lower()).strip("-")
    return s or "skill"


def _proposals_dir() -> Path:
    override = os.environ.get(ENV_PROPOSALS_DIR)
    if override:
        return Path(override)
    return Path.home() / ".nuvel" / "skill-proposals"


def _default_llm_fn(prompt: str) -> str:
    """Production LLM call — uses google.genai already pulled in by ADK."""
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(f"google.genai unavailable: {exc}") from exc

    model = os.environ.get(ENV_MODEL, DEFAULT_MODEL)
    client = genai.Client()
    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return getattr(resp, "text", "") or ""


# ── Plugin ────────────────────────────────────────────────────────────


class SkillCuratorPlugin(BasePlugin):
    """ADK Plugin that proposes new/patched skills after complex runs.

    Hooks: ``before_run_callback`` (reset), ``after_tool_callback`` and
    ``on_event_callback`` (accumulate), ``on_tool_error_callback`` (count
    repeated errors), ``after_run_callback`` (evaluate + dispatch).

    The ``llm_fn`` argument is for tests; production code leaves it
    ``None`` and we call ``google.genai`` lazily.
    """

    def __init__(self, llm_fn: Optional[LlmFn] = None) -> None:
        super().__init__(name="skill_curator")
        self._llm_fn: LlmFn = llm_fn or _default_llm_fn
        self._tool_calls: int = 0
        self._event_count: int = 0
        self._tool_errors: dict[str, int] = {}
        self._triggering_agents: set[str] = set()
        # Compact transcript snippets captured during the run (bounded).
        self._transcript_lines: list[str] = []
        self._transcript_chars: int = 0
        self._transcript_max: int = 4000

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def before_run_callback(
        self, *, invocation_context: "InvocationContext"
    ) -> None:
        self._tool_calls = 0
        self._event_count = 0
        self._tool_errors = {}
        self._triggering_agents = set()
        self._transcript_lines = []
        self._transcript_chars = 0
        return None

    async def after_tool_callback(
        self,
        *,
        tool: "BaseTool",
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
        result: dict,
    ) -> None:
        # Tool calls are also surfaced via on_event_callback (function_call
        # parts), but counting here is more reliable across agent variants.
        self._tool_calls += 1
        return None

    async def on_tool_error_callback(
        self,
        *,
        tool: "BaseTool",
        tool_args: dict[str, Any],
        tool_context: "ToolContext",
        error: Exception,
    ) -> None:
        name = getattr(tool, "name", "?") or "?"
        self._tool_errors[name] = self._tool_errors.get(name, 0) + 1
        self._append_transcript(
            f"tool_error: {name} ({type(error).__name__}: {str(error)[:120]})"
        )
        return None

    async def on_event_callback(
        self, *, invocation_context: "InvocationContext", event: "Event"
    ) -> None:
        self._event_count += 1
        author = getattr(event, "author", None) or "?"
        if author and author != "?":
            self._triggering_agents.add(author)
        content = getattr(event, "content", None)
        if content is not None:
            for part in getattr(content, "parts", None) or []:
                fc = getattr(part, "function_call", None)
                text = getattr(part, "text", None)
                if fc is not None:
                    self._append_transcript(
                        f"[{author}] tool_call: {getattr(fc, 'name', '?')}"
                    )
                elif text:
                    snippet = text.strip().replace("\n", " ")
                    if len(snippet) > 240:
                        snippet = snippet[:240] + "..."
                    self._append_transcript(f"[{author}] {snippet}")
        return None

    async def after_run_callback(
        self, *, invocation_context: "InvocationContext"
    ) -> None:
        if not _enabled():
            return None

        min_tools = _int_env(ENV_MIN_TOOLS, DEFAULT_MIN_TOOLS)
        min_events = _int_env(ENV_MIN_EVENTS, DEFAULT_MIN_EVENTS)
        max_tool_errors = max(
            self._tool_errors.values(), default=0
        )
        if not self._is_complex(
            tool_calls=self._tool_calls,
            event_count=self._event_count,
            error_count=max_tool_errors,
            min_tools=min_tools,
            min_events=min_events,
        ):
            return None

        # In a generated agent, skills live alongside plugins under the
        # agent package: <pkg>/plugins/<this file> and <pkg>/skills/.
        skills_dir = Path(
            os.environ.get(ENV_SKILLS_DIR)
            or Path(__file__).resolve().parent.parent / "skills"
        )
        existing = _existing_skill_names(skills_dir)
        prompt = self._build_prompt(existing)

        try:
            raw = self._llm_fn(prompt)
        except Exception as exc:
            logger.warning("[skill_curator] LLM call failed: %s", exc)
            return None

        try:
            proposal = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("[skill_curator] malformed JSON from curator; skipping")
            return None

        action = proposal.get("action")
        if action not in VALID_ACTIONS:
            logger.warning("[skill_curator] unknown action %r; skipping", action)
            return None
        if action == "noop":
            logger.info("[skill_curator] noop — no proposal written")
            return None

        # Resolve agent attribution: prefer aggregated set, fall back to root.
        agents = sorted(self._triggering_agents) or [
            getattr(getattr(invocation_context, "agent", None), "name", "") or ""
        ]
        try:
            path = self._write_proposal(proposal, agents)
        except OSError as exc:
            logger.warning("[skill_curator] failed to write proposal: %s", exc)
            return None
        logger.info(
            "[skill_curator] proposal written: %s (action=%s)", path, action
        )
        return None

    # ── Internal helpers ──────────────────────────────────────────────

    def _append_transcript(self, line: str) -> None:
        if self._transcript_chars >= self._transcript_max:
            return
        self._transcript_lines.append(line)
        self._transcript_chars += len(line) + 1

    def _is_complex(
        self,
        *,
        tool_calls: int,
        event_count: int,
        error_count: int,
        min_tools: int,
        min_events: int,
    ) -> bool:
        if event_count >= min_events:
            return True
        if tool_calls >= min_tools:
            return True
        # Strong "missing skill" signal: same tool errored repeatedly.
        min_errors = _int_env(ENV_MIN_ERRORS, DEFAULT_MIN_ERRORS)
        if error_count >= min_errors:
            return True
        return False

    def _build_prompt(self, existing: list[str]) -> str:
        transcript = "\n".join(self._transcript_lines)
        if len(transcript) > self._transcript_max:
            transcript = transcript[: self._transcript_max] + "\n...[truncated]"
        # Surface error signal explicitly — it's a strong "missing skill" hint.
        repeated_errors = {
            tool: count
            for tool, count in self._tool_errors.items()
            if count >= _int_env(ENV_MIN_ERRORS, DEFAULT_MIN_ERRORS)
        }
        error_section = ""
        if repeated_errors:
            error_section = (
                f"\n\nRepeated tool errors (>=3 occurrences): "
                f"{json.dumps(repeated_errors)}"
            )
        return (
            "You are a skill curator for an agent. Inspect the trajectory below "
            "and decide if a NEW skill should be proposed, an EXISTING skill "
            "patched, or no action taken.\n\n"
            "Reply with STRICT JSON only, no markdown, matching exactly:\n"
            '{"action": "noop"|"propose_new"|"patch_existing", '
            '"skill_name": "kebab-case-name", '
            '"rationale": "one paragraph", '
            '"patch": "skill body or diff-style instructions"}\n\n'
            f"Existing skills: {existing}"
            f"{error_section}\n\n"
            "Trajectory (compact):\n"
            f"{transcript}\n"
        )

    def _write_proposal(self, proposal: dict, agents: list[str]) -> Path:
        out_dir = _proposals_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = _slugify(proposal.get("skill_name") or "skill")
        path = out_dir / f"{ts}-{slug}.md"
        agents_yaml = "[" + ", ".join(agents) + "]"
        body = (
            f"---\n"
            f"action: {proposal.get('action')}\n"
            f"skill_name: {proposal.get('skill_name', '')}\n"
            f"triggering_agents: {agents_yaml}\n"
            f"timestamp: {ts}\n"
            f"---\n\n"
            f"# Skill curator proposal: "
            f"{proposal.get('skill_name', '(unnamed)')}\n\n"
            f"**Action:** `{proposal.get('action')}`\n\n"
            f"**Triggering agents:** {', '.join(agents)}\n\n"
            f"## Rationale\n\n{proposal.get('rationale', '').strip()}\n\n"
            f"## Patch / body\n\n{proposal.get('patch', '').strip()}\n"
        )
        path.write_text(body)
        return path


__all__ = ["SkillCuratorPlugin"]
