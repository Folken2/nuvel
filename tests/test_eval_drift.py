"""Drift detection: window math and threshold edge cases."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nuvel.eval.drift import detect_drift
from nuvel.eval.schema import SCORER_VERSION, ScoredRun


_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)


def _scored(agent: str, days_ago: float, overall: float) -> ScoredRun:
    ts = _NOW - timedelta(days=days_ago)
    return ScoredRun(
        trace_id=f"{agent}-{days_ago}-{overall}",
        agent=agent,
        scored_at=ts.isoformat(),
        scorer_version=SCORER_VERSION,
        rubric_version="r",
        overall=overall,
        components={"success": overall},
    )


def test_detect_drift_no_data_returns_empty() -> None:
    assert detect_drift([], now=_NOW) == []


def test_detect_drift_flat_no_drift() -> None:
    # Both windows identical → delta ≈ 0
    rows = [_scored("a", 1, 0.8) for _ in range(5)]
    rows += [_scored("a", 9, 0.8) for _ in range(5)]
    rep = detect_drift(rows, window_days=7, now=_NOW)
    assert len(rep) == 1
    r = rep[0]
    assert r.agent == "a"
    assert r.delta == pytest.approx(0.0)
    assert not r.drifted


def test_detect_drift_drop_flags() -> None:
    rows = [_scored("a", 1, 0.5) for _ in range(3)]   # current window, mean 0.5
    rows += [_scored("a", 9, 0.9) for _ in range(3)]  # baseline window, mean 0.9
    rep = detect_drift(rows, window_days=7, threshold=0.1, now=_NOW)
    r = rep[0]
    assert r.delta == pytest.approx(-0.4)
    assert r.drifted


def test_detect_drift_below_threshold_does_not_flag() -> None:
    rows = [_scored("a", 1, 0.85)] * 3 + [_scored("a", 9, 0.90)] * 3
    rep = detect_drift(rows, window_days=7, threshold=0.1, now=_NOW)
    assert rep[0].delta == pytest.approx(-0.05)
    assert not rep[0].drifted


def test_detect_drift_groups_by_agent() -> None:
    rows = [
        _scored("a", 1, 0.5),
        _scored("a", 9, 0.9),
        _scored("b", 1, 0.9),
        _scored("b", 9, 0.5),
    ]
    rep = detect_drift(rows, window_days=7, threshold=0.1, now=_NOW)
    by_agent = {r.agent: r for r in rep}
    assert by_agent["a"].delta < 0 and by_agent["a"].drifted
    assert by_agent["b"].delta > 0 and by_agent["b"].drifted


def test_detect_drift_handles_empty_baseline() -> None:
    rows = [_scored("a", 1, 0.5)]
    rep = detect_drift(rows, window_days=7, now=_NOW)
    r = rep[0]
    assert r.baseline_mean is None
    assert r.delta is None
    assert not r.drifted


def test_detect_drift_rejects_zero_window() -> None:
    with pytest.raises(ValueError):
        detect_drift([], window_days=0)


def test_detect_drift_naive_timestamps_treated_as_utc() -> None:
    naive_ts = (_NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()
    row = ScoredRun(
        trace_id="x",
        agent="a",
        scored_at=naive_ts,
        scorer_version=SCORER_VERSION,
        rubric_version="r",
        overall=0.5,
        components={"success": 0.5},
    )
    rep = detect_drift([row], window_days=7, now=_NOW)
    # Should fall into the current window.
    assert rep[0].current_n == 1
