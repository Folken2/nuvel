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
# wrapper can tell a fresh halt from one already surfaced to the user. Holds
# the id of the invocation that surfaced it (``True`` when none is known).
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

    The halt stops the turn it fired in. Once its envelope has reached the user,
    their next message (a new invocation) is the acknowledgement: the latch and
    the guards' counters are cleared and the model runs again. Without this the
    session could never recover, since the model never runs while latched and
    so can never call the ``acknowledge_halt`` tool itself.
    """
    state = callback_context.state
    reason = state.get(HALT_REASON_STATE_KEY)
    if not reason:
        return None
    invocation_id = getattr(callback_context, "invocation_id", None) or None
    delivered_in = state.get(HALT_HANDOFF_DELIVERED_STATE_KEY)
    if invocation_id and isinstance(delivered_in, str) and delivered_in != invocation_id:
        acknowledge_halt_tool(state)
        return None
    state[HALT_HANDOFF_DELIVERED_STATE_KEY] = invocation_id or True
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


def _reset_latching_guards(state: Any) -> None:
    """Reset the counters of guards that latch, so an at-threshold counter
    doesn't re-trip the halt on the very next event after acknowledgement.

    Imported lazily: ``no_progress`` and ``repeated_failure`` both import from
    this module, so a top-level import here would be circular.
    """
    try:
        from .no_progress import NoProgressGuard

        NoProgressGuard.reset(state)
    except Exception:  # a guard absent or refactored away must not break recovery
        pass
    try:
        from .repeated_failure import RepeatedFailureGuard

        RepeatedFailureGuard.reset(state)
    except Exception:
        pass


def acknowledge_halt_tool(state: Any) -> dict[str, Any]:
    """Lift a latched halt so the session can resume, reporting the reason.

    Exposed to the agent as the ``acknowledge_halt`` tool: when a turn comes
    back as ``[halted: <reason>]``, calling this clears the latch and lets the
    next model call run again. Beyond clearing ``HALT_REASON_STATE_KEY`` it also
    clears the handoff flag and resets the latching guards' counters — otherwise
    a counter already at threshold would immediately re-trip the halt.

    Returns a status dict; ``message`` is a human-readable summary suitable for
    handing straight back to the model.
    """
    reason = state.get(HALT_REASON_STATE_KEY)
    if not reason:
        return {"status": "no_halt", "message": "No halt signal found."}
    acknowledge_halt(state)
    reset_halt_handoff(state)
    _reset_latching_guards(state)
    return {
        "status": "acknowledged",
        "reason": reason,
        "message": f"Halt acknowledged. Reason was: {reason}. Session resumed.",
    }


__all__ = [
    "HALT_REASON_STATE_KEY",
    "HALT_HANDOFF_DELIVERED_STATE_KEY",
    "halt_content",
    "halt_consumer_callback",
    "latch_halt",
    "acknowledge_halt",
    "acknowledge_halt_tool",
    "reset_halt_handoff",
]
