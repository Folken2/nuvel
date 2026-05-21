"""Baseline percentile computation."""
from __future__ import annotations

from pathlib import Path

from nuvel.eval.stats import _percentile, compute_baseline_stats
from nuvel.traces_cli import Run


def _make_run(agent: str, *, cost: float | None = None, dur: int | None = None,
              tokens: int = 0) -> Run:
    return Run(
        agent=agent,
        file=Path("/tmp/x"),
        session_id="s",
        trace_id="t",
        cost_usd=cost,
        duration_ms=dur,
        total_tokens=tokens,
    )


def test_percentile_single_value() -> None:
    assert _percentile([5.0], 95.0) == 5.0


def test_percentile_basic() -> None:
    # 0..100 inclusive, 101 samples → p50 == 50, p95 == 95.
    vals = [float(i) for i in range(101)]
    assert _percentile(vals, 50.0) == 50.0
    assert _percentile(vals, 95.0) == 95.0
    assert _percentile(vals, 99.0) == 99.0


def test_percentile_empty() -> None:
    assert _percentile([], 95.0) is None


def test_compute_baseline_stats_groups_by_agent() -> None:
    runs = [
        _make_run("outlook-king", cost=0.001, dur=100, tokens=500),
        _make_run("outlook-king", cost=0.002, dur=200, tokens=1000),
        _make_run("word-king",   cost=0.005, dur=300, tokens=2000),
    ]
    out = compute_baseline_stats(runs)
    assert set(out.keys()) == {"outlook-king", "word-king"}
    assert out["outlook-king"].sample_size == 2
    assert out["word-king"].sample_size == 1


def test_compute_baseline_stats_window_caps_sample() -> None:
    runs = [_make_run("a", cost=float(i), dur=i, tokens=i) for i in range(200)]
    out = compute_baseline_stats(runs, window=50)
    assert out["a"].sample_size == 50


def test_compute_baseline_stats_handles_subagent_suffix() -> None:
    runs = [
        _make_run("outlook-king/composer", cost=0.001),
        _make_run("outlook-king",          cost=0.002),
    ]
    out = compute_baseline_stats(runs)
    # Both should bucket under "outlook-king".
    assert out["outlook-king"].sample_size == 2


def test_compute_baseline_stats_missing_values_ignored() -> None:
    runs = [
        _make_run("a", cost=None, dur=None, tokens=0),
        _make_run("a", cost=0.01, dur=50, tokens=100),
    ]
    out = compute_baseline_stats(runs)
    # Only one value contributed → percentile equals that single value.
    assert out["a"].p95_cost_usd == 0.01
    assert out["a"].p95_duration_ms == 50.0
    assert out["a"].p99_total_tokens == 100.0
