"""Post-task skill curator — Hermes-Agent-inspired self-improving skills loop.

After a complex agent run, this `after_agent_callback` introspects the
session transcript and asks the agent's own LLM whether the run reveals
either (a) a *new* reusable pattern worth turning into a skill, or
(b) a missing edge case in an existing skill.

Safety boundaries:

* **Off by default.** Only runs when ``NUVEL_SKILL_CURATOR=1``.
* **Never auto-applies.** Proposals are written to
  ``~/.nuvel/skill-proposals/<timestamp>-<name>.md`` (override with
  ``NUVEL_SKILL_PROPOSALS_DIR``) for human review. The proposal directory
  lives outside the project tree so proposals don't accidentally land in
  a commit.
* **No new third-party deps.** The LLM call is injected via ``llm_fn``
  (tests mock it); the production wiring uses ``google.genai`` already
  pulled in by ADK.

ADK callback contract: ADK matches by parameter name, so the public
function takes ``callback_context`` keyword and returns ``None``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

# Environment knobs.
ENV_ENABLED = "NUVEL_SKILL_CURATOR"
ENV_MIN_TOOLS = "NUVEL_SKILL_CURATOR_MIN_TOOLS"
ENV_MIN_EVENTS = "NUVEL_SKILL_CURATOR_MIN_EVENTS"
ENV_PROPOSALS_DIR = "NUVEL_SKILL_PROPOSALS_DIR"
ENV_SKILLS_DIR = "NUVEL_SKILLS_DIR"
ENV_MODEL = "NUVEL_SKILL_CURATOR_MODEL"

DEFAULT_MIN_TOOLS = 5
DEFAULT_MIN_EVENTS = 12
DEFAULT_MODEL = "gemini-2.0-flash"
VALID_ACTIONS = {"noop", "propose_new", "patch_existing"}

LlmFn = Callable[[str], str]


def _enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _count_tool_calls(events: Iterable[Any]) -> int:
    n = 0
    for ev in events or []:
        content = getattr(ev, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            if getattr(part, "function_call", None) is not None:
                n += 1
    return n


def _is_complex(events: Iterable[Any], *, min_tools: int, min_events: int) -> bool:
    events = list(events or [])
    if len(events) >= min_events:
        return True
    return _count_tool_calls(events) >= min_tools


def _existing_skill_names(skills_dir: Path) -> list[str]:
    if not skills_dir.is_dir():
        return []
    out: list[str] = []
    for sub in sorted(skills_dir.iterdir()):
        if sub.is_dir() and (sub / "SKILL.md").is_file():
            out.append(sub.name)
    return out


def _summarize_transcript(events: Iterable[Any], max_chars: int = 4000) -> str:
    """Compact, redaction-friendly transcript summary for the curator prompt."""
    lines: list[str] = []
    for ev in events or []:
        author = getattr(ev, "author", "?") or "?"
        content = getattr(ev, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            fc = getattr(part, "function_call", None)
            if fc is not None:
                lines.append(f"[{author}] tool_call: {getattr(fc, 'name', '?')}")
            elif text:
                snippet = text.strip().replace("\n", " ")
                if len(snippet) > 240:
                    snippet = snippet[:240] + "..."
                lines.append(f"[{author}] {snippet}")
    blob = "\n".join(lines)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n...[truncated]"
    return blob


def _build_prompt(transcript: str, existing: list[str]) -> str:
    return (
        "You are a skill curator for an agent. Inspect the transcript below "
        "and decide if a NEW skill should be proposed, an EXISTING skill "
        "patched, or no action taken.\n\n"
        "Reply with STRICT JSON only, no markdown, matching exactly:\n"
        '{"action": "noop"|"propose_new"|"patch_existing", '
        '"skill_name": "kebab-case-name", '
        '"rationale": "one paragraph", '
        '"patch": "skill body or diff-style instructions"}\n\n'
        f"Existing skills: {existing}\n\n"
        "Transcript (compact):\n"
        f"{transcript}\n"
    )


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


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "skill").lower()).strip("-")
    return s or "skill"


def _proposals_dir() -> Path:
    override = os.environ.get(ENV_PROPOSALS_DIR)
    if override:
        return Path(override)
    return Path.home() / ".nuvel" / "skill-proposals"


def _write_proposal(proposal: dict, agent_name: str) -> Path:
    out_dir = _proposals_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify(proposal.get("skill_name") or "skill")
    path = out_dir / f"{ts}-{slug}.md"
    body = (
        f"---\n"
        f"action: {proposal.get('action')}\n"
        f"skill_name: {proposal.get('skill_name', '')}\n"
        f"agent: {agent_name}\n"
        f"timestamp: {ts}\n"
        f"---\n\n"
        f"# Skill curator proposal: {proposal.get('skill_name', '(unnamed)')}\n\n"
        f"**Action:** `{proposal.get('action')}`\n\n"
        f"## Rationale\n\n{proposal.get('rationale', '').strip()}\n\n"
        f"## Patch / body\n\n{proposal.get('patch', '').strip()}\n"
    )
    path.write_text(body)
    return path


def skill_curator(callback_context: Any = None, *, llm_fn: LlmFn | None = None) -> None:
    """ADK-compatible ``after_agent_callback``.

    Returns ``None`` unconditionally — never modifies agent output. The
    ``llm_fn`` parameter is for tests; production code leaves it ``None``.
    """
    if not _enabled():
        return None

    session = getattr(callback_context, "session", None)
    events = list(getattr(session, "events", None) or []) if session is not None else []

    min_tools = _int_env(ENV_MIN_TOOLS, DEFAULT_MIN_TOOLS)
    min_events = _int_env(ENV_MIN_EVENTS, DEFAULT_MIN_EVENTS)
    if not _is_complex(events, min_tools=min_tools, min_events=min_events):
        return None

    skills_dir = Path(os.environ.get(ENV_SKILLS_DIR) or
                      Path(__file__).resolve().parent.parent / "backends" / "adk" / "skills")
    existing = _existing_skill_names(skills_dir)
    transcript = _summarize_transcript(events)
    prompt = _build_prompt(transcript, existing)

    fn: LlmFn = llm_fn or _default_llm_fn
    try:
        raw = fn(prompt)
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

    agent_name = getattr(callback_context, "agent_name", "") or ""
    try:
        path = _write_proposal(proposal, agent_name)
    except OSError as exc:
        logger.warning("[skill_curator] failed to write proposal: %s", exc)
        return None
    logger.info("[skill_curator] proposal written: %s (action=%s)", path, action)
    return None
