"""Baseline statistics for outlier detection.

Computes per-agent rolling percentiles over the last N runs. Heuristics
consume the result to flag cost/latency/token outliers.
"""
from __future__ import annotations

from dataclasses import dataclass

from nuvel.traces_cli import Run


_DEFAULT_WINDOW = 100


@dataclass
class BaselineStats:
    """Per-agent percentile snapshot."""

    p95_cost_usd: float | None = None
    p95_duration_ms: float | None = None
    p99_total_tokens: float | None = None
    sample_size: int = 0


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. ``pct`` is 0..100."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    rank = (pct / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _agent_key(run: Run) -> str:
    """Top-level agent (drop the in-trace sub-agent suffix like 'foo/bar')."""
    return run.agent.split("/")[0]


def compute_baseline_stats(
    runs: list[Run], *, window: int = _DEFAULT_WINDOW
) -> dict[str, BaselineStats]:
    """Return per-agent percentile snapshots over the most recent ``window`` runs.

    ``runs`` is consumed in given order; callers should pre-sort newest-first
    if they want a strictly rolling window. The CLI passes newest-first.
    """
    by_agent: dict[str, list[Run]] = {}
    for r in runs:
        by_agent.setdefault(_agent_key(r), []).append(r)

    out: dict[str, BaselineStats] = {}
    for agent, agent_runs in by_agent.items():
        sample = agent_runs[:window]
        costs = [r.cost_usd for r in sample if r.cost_usd is not None]
        durations = [float(r.duration_ms) for r in sample if r.duration_ms is not None]
        tokens = [float(r.total_tokens) for r in sample if r.total_tokens]
        out[agent] = BaselineStats(
            p95_cost_usd=_percentile(costs, 95.0),
            p95_duration_ms=_percentile(durations, 95.0),
            p99_total_tokens=_percentile(tokens, 99.0),
            sample_size=len(sample),
        )
    return out
