"""
Judge fork — after-turn self-improvement loop for the meta-agent.

``review_fork_callback`` is an ADK ``after_agent_callback``. After the
meta-agent finishes a turn it hands a throwaway *judge* agent — restricted to
a single durable-memory write plus read-only skill inspection — to the
:data:`SIBLING_RUNNER`. The judge is shown the just-finished conversation and
asked "what, if anything, is worth saving?". Facts are written through the
active :class:`~nuvel.memory.org_memory_service.OrgMemoryService` (when a
memory DB is wired) via the ``remember_fact`` tool.

Design guarantees (mirror of the generated-agent review fork):

* **Never blocks the parent.** The judge runs fire-and-forget on the sibling
  runner; the parent's reply has already gone out.
* **Opt-in.** Disabled unless ``NUVEL_MEMORY_REVIEW_FORK`` is truthy.
* **Throttled.** ``throttle.try_claim`` enforces a cooldown + per-session cap.
* **Recursion guard is structural.** The judge ``LlmAgent`` has *no*
  ``after_agent_callback`` of its own, so it can never spawn a judge of a
  judge; and its toolset is whitelisted to the memory + skill-read tools only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from google.adk.agents import LlmAgent

from nuvel.config.llm import FAST_MODEL

from .fork_utils import format_conversation_snapshot, make_whitelist_callback
from .review_tools import REVIEW_TOOL_NAMES, review_tool_list
from .sibling_runner import SIBLING_RUNNER
from .throttle import try_claim

logger = logging.getLogger(__name__)

REVIEW_FORK_ENABLED_ENV = "NUVEL_MEMORY_REVIEW_FORK"

_JUDGE_NAME = "memory_review_fork"
_JUDGE_INSTRUCTION = (
    "You are a self-improvement curator for the nuvel meta-agent. That agent "
    "has just finished a turn with its user; the conversation is provided to "
    "you as a <CONVERSATION>...</CONVERSATION> block. Decide what, if "
    "anything, is worth carrying into future sessions.\n\n"
    "Your only tools are: remember_fact (write one durable long-term memory) "
    "and list_skills / read_skill (inspect the current skill catalog, "
    "read-only). \n\n"
    "Call remember_fact ONLY for facts that are: about the user or their "
    "environment/project, stable (still true next week), and not already "
    "obvious. Skip transient task state, error noise, and anything "
    "short-lived. When you have saved what's worth saving (or decided nothing "
    "qualifies), stop."
)

_REVIEW_PROMPT = (
    "Review the conversation above and capture anything durable with "
    "remember_fact. If nothing qualifies, simply stop."
)

# Whitelist gate — anything outside the judge's toolset is refused.
_whitelist_callback = make_whitelist_callback(REVIEW_TOOL_NAMES, fork_name="review")

_judge_agent: Optional[LlmAgent] = None


def _build_judge_agent() -> LlmAgent:
    return LlmAgent(
        model=FAST_MODEL,
        name=_JUDGE_NAME,
        description="Throwaway judge that curates durable memory after a turn.",
        instruction=_JUDGE_INSTRUCTION,
        tools=list(review_tool_list),
        before_tool_callback=[_whitelist_callback],
        # NO after_agent_callback — structural recursion guard.
    )


def _get_judge_agent() -> LlmAgent:
    global _judge_agent
    if _judge_agent is None:
        _judge_agent = _build_judge_agent()
    return _judge_agent


def _enabled() -> bool:
    return os.environ.get(REVIEW_FORK_ENABLED_ENV, "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def review_fork_callback(callback_context: Any) -> None:
    """ADK ``after_agent_callback`` — spawn the judge fork, fire-and-forget."""
    if not _enabled():
        return None

    state = getattr(callback_context, "state", None)
    if not try_claim(state, "review"):
        return None

    ictx = getattr(callback_context, "_invocation_context", None)
    if ictx is None:
        return None

    session = getattr(ictx, "session", None)
    events = list(getattr(session, "events", []) or [])
    snapshot = format_conversation_snapshot(events)

    app_name = getattr(ictx, "app_name", "") or ""
    user_id = getattr(ictx, "user_id", "") or ""

    try:
        SIBLING_RUNNER.spawn(
            agent=_get_judge_agent(),
            prompt=f"{snapshot}\n\n{_REVIEW_PROMPT}",
            app_name=app_name,
            user_id=user_id,
            memory_service=getattr(ictx, "memory_service", None),
            log_prefix="review_fork",
        )
    except Exception:
        logger.exception("review_fork: failed to spawn judge")
    return None


__all__ = ["review_fork_callback", "REVIEW_FORK_ENABLED_ENV"]
