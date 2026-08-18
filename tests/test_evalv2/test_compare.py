"""Tests for evalv2 baseline comparison — pure data, no IO."""
from __future__ import annotations

from nuvel.evalv2 import compare_results
from nuvel.evalv2.schema import (
    SCHEMA_VERSION,
    EvalSummary,
    EvalSuiteResult,
    ScoredExample,
)


def _result(scores: dict[str, float | None], *, timestamp: str = "2026-08-07T00:00:00Z"):
    examples = [
        ScoredExample(id=ex_id, input=f"input-{ex_id}", score=score)
        for ex_id, score in scores.items()
    ]
    numeric = [s for s in scores.values() if s is not None]
    overall = sum(numeric) / len(numeric) if numeric else None
    return EvalSuiteResult(
        schema_version=SCHEMA_VERSION,
        skill="summarize",
        suite="summarize-eval",
        timestamp=timestamp,
        summary=EvalSummary(total=len(examples), overall=overall),
        examples=examples,
    )


def test_identical_results_no_regression():
    scores = {"a": 0.9, "b": 0.8}
    report = compare_results(_result(scores), _result(scores))
    assert report.summary["overall_delta"] == 0.0
    assert report.regressed is False
    assert report.regression_count == 0
    assert report.improvement_count == 0
    assert report.summary["ties"] == 2


def test_current_better_positive_delta():
    baseline = _result({"a": 0.6, "b": 0.6})
    current = _result({"a": 0.9, "b": 0.9})
    report = compare_results(current, baseline)
    assert report.summary["overall_delta"] > 0
    assert report.regressed is False
    assert report.improvement_count == 2
    assert report.regression_count == 0


def test_current_worse_flags_regression():
    baseline = _result({"a": 0.9, "b": 0.9})
    current = _result({"a": 0.5, "b": 0.5})
    report = compare_results(current, baseline)
    assert report.summary["overall_delta"] < 0
    assert report.regressed is True
    assert report.regression_count == 2
    assert report.improvement_count == 0


def test_ids_do_not_overlap_only_matching_compared():
    baseline = _result({"a": 0.9, "b": 0.8})
    current = _result({"b": 0.85, "c": 0.7})
    report = compare_results(current, baseline)
    assert report.summary["matched"] == 1
    assert report.summary["only_in_baseline"] == ["a"]
    assert report.summary["only_in_current"] == ["c"]
    assert len(report.examples) == 1
    assert report.examples[0]["id"] == "b"
    assert any("only in baseline" in w for w in report.summary["warnings"])
    assert any("only in current" in w for w in report.summary["warnings"])


def test_threshold_override_flags_small_threshold():
    baseline = _result({"a": 0.9})
    current = _result({"a": 0.6})  # delta -0.30
    # default threshold -0.05 would flag; a stricter (more negative) one still flags
    strict = compare_results(current, baseline, regression_threshold=-0.25)
    assert strict.regressed is True
    # a threshold below the delta does NOT flag
    lenient = compare_results(current, baseline, regression_threshold=-0.50)
    assert lenient.regressed is False


def test_empty_results_report_empty():
    report = compare_results(_result({}), _result({}))
    assert report.examples == []
    assert report.summary["matched"] == 0
    assert report.summary["overall_delta"] is None
    assert report.regressed is False
    assert any("no overlapping" in w for w in report.summary["warnings"])


def test_none_score_is_incomparable_tie():
    baseline = _result({"a": None})
    current = _result({"a": 0.9})
    report = compare_results(current, baseline)
    assert report.examples[0]["delta"] is None
    assert report.examples[0]["verdict"] == "tie"
    assert report.regression_count == 0
    assert report.improvement_count == 0


def test_report_ids_from_timestamps():
    baseline = _result({"a": 0.9}, timestamp="base-ts")
    current = _result({"a": 0.9}, timestamp="curr-ts")
    report = compare_results(current, baseline)
    assert report.baseline_id == "base-ts"
    assert report.current_id == "curr-ts"
