"""Halt latch — a single session-state signal every guard can raise.

A guard that detects a runaway condition (no progress, a tool failing in a
loop) writes a human-readable reason into ``state[HALT_REASON_STATE_KEY]``.
``halt_consumer_callback`` runs as a ``before_model_callback`` and, while that
signal is latched, short-circuits the model call by returning a canonical
``[halted: <reason>]`` envelope instead of letting the model run again.

ADK's ``State`` exposes no ``pop``/``del``, so every "clear" here overwrites
with ``None``; readers use ``state.get(...)`` and treat ``None`` as absent.
"""

from __future__ import annotations

from typing import Any

from google.adk.models import LlmResponse
from google.genai.types import Content, Part

# The latched halt reason (falsy/absent means "not halted").
HALT_REASON_STATE_KEY = "halt_reason"
# Once-per-halt flag: set the first time the halt envelope is handed back so a
# wrapper can tell a fresh halt from one already surfaced to the user.
HALT_HANDOFF_DELIVERED_STATE_KEY = "__halt_handoff_delivered__"


def halt_content(reason: str) -> Content:
    """The canonical ``[halted: <reason>]`` envelope shared by every halt path."""
    return Content(role="model", parts=[Part(text=f"[halted: {reason}]")])


async def halt_consumer_callback(
    *,
    callback_context: Any,
    llm_request: Any = None,
) -> LlmResponse | None:
    """Short-circuit the model call while a halt is latched.

    Returns the halt envelope (an ``LlmResponse``) so ADK skips the actual
    model invocation; returns ``None`` when nothing is latched, letting the
    normal model call proceed.
    """
    state = callback_context.state
    reason = state.get(HALT_REASON_STATE_KEY)
    if not reason:
        return None
    state[HALT_HANDOFF_DELIVERED_STATE_KEY] = True
    return LlmResponse(content=halt_content(reason))


def latch_halt(state: Any, reason: str) -> bool:
    """Latch ``reason`` iff no halt is already set.

    Returns ``True`` when this call wrote the reason, ``False`` when another
    guard beat it to the latch — so guards can express "halt unless someone
    already halted" without repeating the check-and-set at every call site.
    """
    if state.get(HALT_REASON_STATE_KEY):
        return False
    state[HALT_REASON_STATE_KEY] = reason
    return True


def acknowledge_halt(state: Any) -> None:
    """Clear the halt signal so the next model call runs again.

    Called at the user-turn boundary once the user has seen the halt. Guards
    that latched should also ``reset`` their internal counters, otherwise a
    counter already at threshold re-trips the halt on the very next event.
    """
    state[HALT_REASON_STATE_KEY] = None


def reset_halt_handoff(state: Any) -> None:
    """Clear the once-per-halt handoff flag at a user-turn boundary."""
    state[HALT_HANDOFF_DELIVERED_STATE_KEY] = None


__all__ = [
    "HALT_REASON_STATE_KEY",
    "HALT_HANDOFF_DELIVERED_STATE_KEY",
    "halt_content",
    "halt_consumer_callback",
    "latch_halt",
    "acknowledge_halt",
    "reset_halt_handoff",
]
