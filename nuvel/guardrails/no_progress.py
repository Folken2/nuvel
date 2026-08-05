"""``NoProgressGuard`` — latch a halt when the model repeats itself.

Runs as an ``after_model_callback``. If the model emits byte-identical text
``window`` times in a row, the session is in a response loop and burning
tokens with nothing to show for it, so the guard latches a halt.
"""

from __future__ import annotations

from typing import Any

from google.adk.models import LlmResponse

from .halt_consumer import latch_halt

_STREAK_KEY = "__no_progress_streak__"
_LAST_TEXT_KEY = "__no_progress_last_text__"


def _response_text(llm_response: LlmResponse) -> str:
    """Whitespace-normalized concatenation of the response's text parts."""
    content = getattr(llm_response, "content", None)
    if content is None or not getattr(content, "parts", None):
        return ""
    texts = [p.text for p in content.parts if getattr(p, "text", None)]
    return " ".join(" ".join(texts).split())


class NoProgressGuard:
    def __init__(self, *, window: int = 5) -> None:
        if window < 2:
            raise ValueError(
                f"window must be >= 2 (a window of 1 halts on every response); "
                f"got {window}"
            )
        self.window = window

    async def after_model_callback(
        self,
        *,
        callback_context: Any,
        llm_response: LlmResponse,
    ) -> None:
        text = _response_text(llm_response)
        if not text:
            return None

        state = callback_context.state
        previous = state.get(_LAST_TEXT_KEY)
        streak = state.get(_STREAK_KEY, 0)
        streak = streak + 1 if previous == text else 1

        state[_LAST_TEXT_KEY] = text
        state[_STREAK_KEY] = streak

        if streak >= self.window:
            latch_halt(
                state,
                f"no progress: the model produced the same response {streak} "
                "times in a row — halting to break the loop.",
            )
        return None

    @staticmethod
    def reset(state: Any) -> None:
        """Clear the identical-response streak and the last-text marker.

        Without this, a halt cleared at the user-turn boundary re-trips
        immediately: the streak is already at ``window``, so the next identical
        response would satisfy the threshold again.
        """
        state[_STREAK_KEY] = 0
        state[_LAST_TEXT_KEY] = None


__all__ = ["NoProgressGuard"]
