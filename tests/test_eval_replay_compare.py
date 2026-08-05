"""Pure-function comparison of baseline ScoredRuns vs variant ReplayResults."""
from __future__ import annotations

from nuvel.eval.replay.compare import compare
from nuvel.eval.replay.schema import ReplayResult
from nuvel.eval.schema import ScoredRun


def _baseline(trace_id: str, overall: float, quality: float, success: float,
              agent: str = "outlook-king") -> ScoredRun:
    return ScoredRun(
        trace_id=trace_id, agent=agent, scored_at="2026-05-20T00:00:00+00:00",
        scorer_version="1.0", rubric_version="default-1.0", overall=overall,
        components={"success": success, "quality": quality},
    )


def _variant(trace_id: str, overall: float, quality: float, success: float,
             agent: str = "outlook-king") -> ReplayResult:
    return ReplayResult(
        trace_id=trace_id, agent=agent, variant_name="v", variant_version="v-1.0",
        replayed_at="2026-05-21T00:00:00+00:00", model="m", output_text="o",
        replay_cost_usd=0.0,
        scored={"overall": overall, "components": {"quality": quality, "success": success}},
    )


def test_compare_pairs_and_computes_deltas() -> None:
    base = [_baseline("t1", 0.70, 0.6, 0.8), _baseline("t2", 0.80, 0.9, 0.7)]
    var = [_variant("t1", 0.80, 0.8, 0.8), _variant("t2", 0.75, 0.8, 0.7)]
    report = compare(base, var)
    row = report.agents[0]
    assert row.agent == "outlook-king"
    assert row.n == 2
    assert round(row.baseline_overall_mean, 4) == 0.75
    assert round(row.variant_overall_mean, 4) == 0.775
    assert round(row.d_overall, 4) == 0.025      # mean of (+0.10, -0.05)
    assert round(row.d_quality, 4) == 0.05        # mean of (+0.2, -0.1)
    assert row.wins == 1 and row.losses == 1 and row.ties == 0


def test_compare_only_pairs_traces_in_both() -> None:
    base = [_baseline("t1", 0.7, 0.6, 0.8), _baseline("t2", 0.8, 0.9, 0.7)]
    var = [_variant("t1", 0.8, 0.8, 0.8)]  # t2 missing
    report = compare(base, var)
    assert report.agents[0].n == 1


def test_compare_small_sample_flag() -> None:
    base = [_baseline(f"t{i}", 0.7, 0.6, 0.8) for i in range(5)]
    var = [_variant(f"t{i}", 0.7, 0.6, 0.8) for i in range(5)]
    report = compare(base, var)
    assert report.agents[0].small_sample is True


def test_compare_regression_flag() -> None:
    base = [_baseline(f"t{i}", 0.9, 0.9, 0.9) for i in range(3)]
    var = [_variant(f"t{i}", 0.7, 0.7, 0.7) for i in range(3)]  # Δ overall = -0.2
    report = compare(base, var)
    assert report.regressed is True


def test_compare_groups_by_agent() -> None:
    base = [_baseline("t1", 0.7, 0.6, 0.8, agent="a"),
            _baseline("t2", 0.7, 0.6, 0.8, agent="b")]
    var = [_variant("t1", 0.8, 0.7, 0.9, agent="a"),
           _variant("t2", 0.6, 0.5, 0.7, agent="b")]
    report = compare(base, var)
    assert {row.agent for row in report.agents} == {"a", "b"}
