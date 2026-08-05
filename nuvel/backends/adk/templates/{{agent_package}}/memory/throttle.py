"""
Per-session throttle for after-turn "fork" callbacks.

``review_fork_callback`` fires on every finished parent turn and each firing
may spin up a judge run that calls the LLM. On a burst of short user turns
that's wasteful — a second fork launches over a near-identical snapshot while
the first is still in flight. ``try_claim`` guards against that with two
limits kept in session state, keyed per fork type:

* a **cooldown window** between consecutive runs of the same fork type
  (``NUVEL_MEMORY_FORK_COOLDOWN`` seconds, default 120), and
* a **per-session cap** as a runaway-loop safety valve
  (``NUVEL_MEMORY_FORK_CAP``, default 50).

Set ``NUVEL_MEMORY_FORK_COOLDOWN=0`` to disable the cooldown (the cap still
applies) — handy for tests that want every-turn behavior.
"""

from __future__ import annotations

import os
import time
from typing import Any

STATE_KEY = "_nuvel_fork_throttle"

ENV_COOLDOWN = "NUVEL_MEMORY_FORK_COOLDOWN"
ENV_CAP = "NUVEL_MEMORY_FORK_CAP"

DEFAULT_COOLDOWN_SECONDS = 120.0
DEFAULT_PER_SESSION_CAP = 50


def _cooldown_seconds() -> float:
    raw = os.environ.get(ENV_COOLDOWN)
    if raw is None:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS


def _per_session_cap() -> int:
    raw = os.environ.get(ENV_CAP)
    if raw is None:
        return DEFAULT_PER_SESSION_CAP
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_PER_SESSION_CAP


def try_claim(state: Any, fork_type: str, *, now: float | None = None) -> bool:
    """Try to claim a fork slot for ``fork_type`` on this turn.

    Returns ``True`` (and records the run in ``state``) when the cooldown has
    elapsed and the per-session cap is not yet reached; returns ``False``
    (state unchanged) otherwise. Treat ``False`` as "skip this turn".

    ``state`` is the ADK session state (a mutable mapping). ``None`` is
    treated as "no throttling available" and always claims.
    """
    if state is None:
        return True

    ts = time.time() if now is None else now
    raw = state.get(STATE_KEY)
    throttle: dict[str, Any] = raw if isinstance(raw, dict) else {}

    entry = throttle.get(fork_type)
    if not isinstance(entry, dict):
        entry = {}
    try:
        count = int(entry.get("count", 0) or 0)
        last_at = float(entry.get("last_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        count, last_at = 0, 0.0

    cap = _per_session_cap()
    if cap and count >= cap:
        return False

    cooldown = _cooldown_seconds()
    if cooldown > 0 and last_at > 0 and (ts - last_at) < cooldown:
        return False

    throttle[fork_type] = {"count": count + 1, "last_at": ts}
    state[STATE_KEY] = throttle
    return True


__all__ = ["try_claim", "STATE_KEY", "ENV_COOLDOWN", "ENV_CAP"]
