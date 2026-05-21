"""Drift detection: rolling-window mean comparison per agent.

Compares the mean ``overall`` over the most recent ``window_days`` to
the same-length window immediately before it. Flags agents whose
absolute delta crosses ``threshold``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nuvel.eval.schema import ScoredRun


_DEFAULT_THRESHOLD = 0.1


@dataclass
class DriftReport:
    """One agent's drift status."""

    agent: str
    current_mean: float | None
    baseline_mean: float | None
    delta: float | None
    current_n: int
    baseline_n: int
    drifted: bool


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _agent_key(scored: ScoredRun) -> str:
    return scored.agent.split("/")[0]


def detect_drift(
    scored: list[ScoredRun],
    *,
    window_days: int = 7,
    threshold: float = _DEFAULT_THRESHOLD,
    now: datetime | None = None,
) -> list[DriftReport]:
    """Compute drift per agent. ``now`` defaults to wall-clock UTC."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    now = now or datetime.now(timezone.utc)
    current_start = now - timedelta(days=window_days)
    baseline_start = current_start - timedelta(days=window_days)

    by_agent: dict[str, list[ScoredRun]] = {}
    for s in scored:
        by_agent.setdefault(_agent_key(s), []).append(s)

    reports: list[DriftReport] = []
    for agent, rows in sorted(by_agent.items()):
        cur, base = [], []
        for s in rows:
            ts = _parse_iso(s.scored_at)
            if ts is None:
                continue
            # Normalize tz-naive timestamps to UTC for comparison.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if current_start <= ts <= now:
                cur.append(s.overall)
            elif baseline_start <= ts < current_start:
                base.append(s.overall)

        cur_mean = sum(cur) / len(cur) if cur else None
        base_mean = sum(base) / len(base) if base else None
        delta = (cur_mean - base_mean) if (cur_mean is not None and base_mean is not None) else None
        drifted = delta is not None and abs(delta) >= threshold
        reports.append(DriftReport(
            agent=agent,
            current_mean=cur_mean,
            baseline_mean=base_mean,
            delta=delta,
            current_n=len(cur),
            baseline_n=len(base),
            drifted=drifted,
        ))
    return reports
