"""Schema round-trip and constants."""
from __future__ import annotations

import json

from nuvel.eval import SCORER_VERSION, Flag, JudgeResult, ScoredRun


def test_scorer_version_is_string() -> None:
    assert isinstance(SCORER_VERSION, str)
    assert SCORER_VERSION  # non-empty


def test_flag_constants_are_unique_strings() -> None:
    values = [
        Flag.TOOL_ERROR,
        Flag.NO_ASSISTANT_OUTPUT,
        Flag.EXCESSIVE_TURNS,
        Flag.COST_OUTLIER,
        Flag.LATENCY_OUTLIER,
        Flag.TOOL_LOOP,
        Flag.TOKEN_BLOAT,
        Flag.INCOMPLETE_TRACE,
    ]
    assert all(isinstance(v, str) for v in values)
    assert len(set(values)) == len(values)


def test_scored_run_round_trip() -> None:
    scored = ScoredRun(
        trace_id="abc123",
        agent="outlook-king",
        scored_at="2026-05-20T14:00:00+00:00",
        scorer_version=SCORER_VERSION,
        rubric_version="default-1.0",
        overall=0.78,
        components={"success": 1.0, "quality": 0.7, "efficiency": 0.85, "reliability": 1.0},
        flags=[Flag.LATENCY_OUTLIER],
        judge={"model": "kimi", "cost_usd": 0.0004, "notes": "ok"},
        skipped_judge=False,
    )
    line = scored.to_json_line()
    # Single line — no embedded newlines.
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["trace_id"] == "abc123"
    restored = ScoredRun.from_json_line(line)
    assert restored == scored


def test_scored_run_defaults_serialize() -> None:
    scored = ScoredRun(
        trace_id="t",
        agent="a",
        scored_at="2026-01-01T00:00:00+00:00",
        scorer_version=SCORER_VERSION,
        rubric_version="r",
        overall=0.5,
        components={"success": 0.5},
    )
    parsed = json.loads(scored.to_json_line())
    assert parsed["flags"] == []
    assert parsed["judge"] == {}
    assert parsed["skipped_judge"] is False


def test_judge_result_ok_property() -> None:
    good = JudgeResult(model="x", success=1.0, quality=0.8, cost_usd=0.0001)
    assert good.ok
    bad = JudgeResult(model="x", error="HTTP 500")
    assert not bad.ok
