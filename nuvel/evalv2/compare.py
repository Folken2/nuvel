"""Baseline comparison for evalv2 — pure, IO-free score diffing.

`compare_results` pairs two `EvalSuiteResult` runs by example id and computes
per-example deltas plus an overall verdict. There is no filesystem or model
access here: the CLI loads the two results, this module diffs them, and the
CLI decides what to render and which exit code to return.

A negative overall delta beyond ``regression_threshold`` marks the run as
``regressed`` — the signal CI keys off of.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schema import EvalSuiteResult


# Deltas smaller than this in magnitude are treated as noise (a tie).
_TIE_BAND = 0.01


@dataclass
class ComparisonReport:
    """The diff between a current run and its baseline.

    ``summary`` carries the aggregate view (overall scores, delta, win/loss/tie
    counts, and any non-overlapping ids); ``examples`` holds one ordered dict
    per matched example. ``regressed`` is the CI verdict.
    """

    baseline_id: str
    current_id: str
    summary: dict = field(default_factory=dict)
    examples: list[dict] = field(default_factory=list)
    regression_count: int = 0
    improvement_count: int = 0
    regressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "current_id": self.current_id,
            "summary": dict(self.summary),
            "examples": [dict(e) for e in self.examples],
            "regression_count": self.regression_count,
            "improvement_count": self.improvement_count,
            "regressed": self.regressed,
        }


def _by_id(result: EvalSuiteResult) -> dict[str, Any]:
    return {ex.id: ex for ex in result.examples}


def _classify(delta: float | None) -> str:
    """Bucket a per-example delta into win / loss / tie."""
    if delta is None:
        return "tie"
    if delta > _TIE_BAND:
        return "win"
    if delta < -_TIE_BAND:
        return "loss"
    return "tie"


def compare_results(
    current: EvalSuiteResult,
    baseline: EvalSuiteResult,
    regression_threshold: float = -0.05,
) -> ComparisonReport:
    """Compare two suite runs, pairing examples by id.

    Only ids present in *both* runs are diffed; ids unique to one side are
    reported in the summary as ``only_in_baseline`` / ``only_in_current`` with
    a warning, but never counted as wins or losses. An example whose score is
    ``None`` on either side is incomparable and counts as a tie.

    ``regressed`` is ``True`` when the overall delta
    (``current.overall - baseline.overall``) falls below
    ``regression_threshold``. When either overall is ``None`` (e.g. an empty
    run), the delta is ``None`` and the run is never marked regressed.
    """
    baseline_map = _by_id(baseline)
    current_map = _by_id(current)

    matched_ids = sorted(set(baseline_map) & set(current_map))
    only_in_baseline = sorted(set(baseline_map) - set(current_map))
    only_in_current = sorted(set(current_map) - set(baseline_map))

    examples: list[dict] = []
    wins = losses = ties = 0
    for ex_id in matched_ids:
        b_score = baseline_map[ex_id].score
        c_score = current_map[ex_id].score
        delta = c_score - b_score if (b_score is not None and c_score is not None) else None
        verdict = _classify(delta)
        if verdict == "win":
            wins += 1
        elif verdict == "loss":
            losses += 1
        else:
            ties += 1
        examples.append(
            {
                "id": ex_id,
                "baseline_score": b_score,
                "current_score": c_score,
                "delta": delta,
                "verdict": verdict,
            }
        )

    b_overall = baseline.summary.overall
    c_overall = current.summary.overall
    overall_delta = (
        c_overall - b_overall
        if (b_overall is not None and c_overall is not None)
        else None
    )
    regressed = overall_delta is not None and overall_delta < regression_threshold

    warnings: list[str] = []
    if only_in_baseline:
        warnings.append(
            f"{len(only_in_baseline)} example(s) only in baseline (dropped): "
            f"{', '.join(only_in_baseline)}"
        )
    if only_in_current:
        warnings.append(
            f"{len(only_in_current)} example(s) only in current (added): "
            f"{', '.join(only_in_current)}"
        )
    if not matched_ids:
        warnings.append("no overlapping example ids — nothing to compare")

    summary = {
        "baseline_overall": b_overall,
        "current_overall": c_overall,
        "overall_delta": overall_delta,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "matched": len(matched_ids),
        "only_in_baseline": only_in_baseline,
        "only_in_current": only_in_current,
        "regression_threshold": regression_threshold,
        "warnings": warnings,
    }

    return ComparisonReport(
        baseline_id=baseline.timestamp,
        current_id=current.timestamp,
        summary=summary,
        examples=examples,
        regression_count=losses,
        improvement_count=wins,
        regressed=regressed,
    )
