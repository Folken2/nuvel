"""``RepeatedFailureGuard`` — latch a halt when a tool keeps failing identically.

Runs as an ``after_tool_callback``. Each failing call is fingerprinted by
``tool_name`` + a SHA-256 of its canonical arguments; when the same signature
fails ``threshold`` times in a row the guard latches a halt (the model is stuck
retrying the same call). A successful call clears that signature's streak, and
streaks older than ``_DECAY_SECONDS`` are pruned so a transient failure early in
a long session doesn't count toward a halt hours later.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from typing import Any

from .halt_consumer import latch_halt

_STREAK_KEY = "__repeated_failure_streak__"

# Surfaced back to the model on its next turn (e.g. via a system-reminder tail)
# so it can see *why* the last tool call failed.
LAST_ERROR_STATE_KEY = "last_error"

# Streak entries older than this are stale and reset to zero.
_DECAY_SECONDS = 600.0


def _signature(tool_name: str, args: Mapping[str, Any] | None) -> str:
    canonical = json.dumps(
        dict(args or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{tool_name}:{digest}"


def _is_failure(tool_response: Any) -> bool:
    if not isinstance(tool_response, Mapping):
        return False
    if "error" in tool_response:
        return True
    exit_code = tool_response.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return True
    return tool_response.get("success") is False


def _error_text(tool_response: Mapping[str, Any]) -> str:
    error = tool_response.get("error")
    if error:
        return str(error)
    stderr = tool_response.get("stderr")
    exit_code = tool_response.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return f"exit_code={exit_code}" + (f": {stderr}" if stderr else "")
    return str(dict(tool_response))


def _load_streaks(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for sig, entry in raw.items():
        if isinstance(entry, dict) and "count" in entry:
            out[sig] = {
                "count": int(entry.get("count", 0)),
                "last_failure_at": float(entry.get("last_failure_at", 0.0)),
            }
    return out


def _prune_stale(streaks: dict[str, dict[str, Any]], now: float) -> None:
    for sig in [
        s
        for s, e in streaks.items()
        if now - e.get("last_failure_at", 0.0) > _DECAY_SECONDS
    ]:
        streaks.pop(sig, None)


class RepeatedFailureGuard:
    def __init__(self, *, threshold: int = 3) -> None:
        if threshold < 2:
            raise ValueError(
                f"threshold must be >= 2 (threshold=1 halts on the first "
                f"failure); got {threshold}"
            )
        self.threshold = threshold

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        args: Mapping[str, Any] | None,
        tool_response: Any,
        tool_context: Any,
    ) -> None:
        state = tool_context.state
        tool_name = getattr(tool, "name", "") or ""
        signature = _signature(tool_name, args)
        now = time.time()

        streaks = _load_streaks(state.get(_STREAK_KEY))
        _prune_stale(streaks, now)

        if _is_failure(tool_response):
            prior = streaks.get(signature, {}).get("count", 0)
            count = prior + 1
            streaks[signature] = {"count": count, "last_failure_at": now}
            state[_STREAK_KEY] = streaks
            state[LAST_ERROR_STATE_KEY] = _error_text(tool_response)

            if count >= self.threshold:
                latch_halt(
                    state,
                    f"{tool_name} failed {count} times in a row with the same "
                    "arguments — halting to break the tool loop.",
                )
        else:
            if signature in streaks:
                streaks.pop(signature, None)
                state[_STREAK_KEY] = streaks
            state[LAST_ERROR_STATE_KEY] = None
        return None

    @staticmethod
    def reset(state: Any) -> None:
        """Clear all per-tool failure streaks.

        Without this, a halt cleared at the user-turn boundary re-fires on the
        next failure: the streak is already at ``threshold``, so one more
        failing call trips it again.
        """
        state[_STREAK_KEY] = None


__all__ = ["RepeatedFailureGuard", "LAST_ERROR_STATE_KEY"]
