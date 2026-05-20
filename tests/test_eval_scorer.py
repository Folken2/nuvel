"""Scorer orchestrator paths.

End-to-end: synthesize a tiny trace directory, run ScoreSession with a
mocked judge, verify scored.jsonl is written and idempotency holds.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.eval.heuristics import HeuristicResult
from nuvel.eval.rubric import DEFAULT_RUBRIC, Rubric
from nuvel.eval.schema import JudgeResult, ScoredRun
from nuvel.eval.scorer import ScoreSession, _weighted_overall, score_run
from nuvel.eval.writer import load_scored_index
from nuvel.traces_cli import Run


# ── score_run unit paths ─────────────────────────────────────────────


def _run(events: list[dict] | None = None, **kw) -> Run:
    base = dict(
        agent="test",
        file=Path("/tmp/x.jsonl"),
        session_id="s",
        trace_id="t1",
        user_input="hi",
        completion_tokens=5,
        llm_calls=1,
        events=events or [
            {"event": "run_start", "user_input": "hi"},
            {"event": "llm_response", "response_text": "hi", "usage": {"completion_tokens": 5}},
            {"event": "run_end"},
        ],
        ended_at="2026-05-20T00:00:00+00:00",
    )
    base.update(kw)
    return Run(**base)


def test_weighted_overall_basic() -> None:
    out = _weighted_overall(
        {"success": 1.0, "quality": 0.5, "efficiency": 1.0, "reliability": 1.0},
        {"success": 0.4, "quality": 0.3, "efficiency": 0.15, "reliability": 0.15},
    )
    assert out == pytest.approx(0.4 + 0.15 + 0.15 + 0.15)


def test_weighted_overall_renormalizes_when_components_missing() -> None:
    # If only success is present, overall == success.
    out = _weighted_overall({"success": 0.8}, {"success": 0.4, "quality": 0.3})
    assert out == pytest.approx(0.8)


async def test_score_run_happy_path_with_judge() -> None:
    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", success=0.9, quality=0.8, cost_usd=0.0003)

    scored = await score_run(_run(), judge_fn=fake_judge)
    assert scored.components["success"] == 0.9
    assert scored.components["quality"] == 0.8
    assert scored.judge["cost_usd"] == 0.0003
    assert not scored.skipped_judge


async def test_score_run_heuristic_floor_skips_judge() -> None:
    called = []

    async def fake_judge(run, rubric) -> JudgeResult:
        called.append(1)
        return JudgeResult(model="m", success=1.0, quality=1.0)

    no_output_run = _run(
        completion_tokens=0,
        events=[{"event": "run_start"}, {"event": "run_end"}],
    )
    scored = await score_run(no_output_run, judge_fn=fake_judge)
    assert called == []
    assert scored.skipped_judge
    assert scored.components["success"] == 0.0
    assert scored.components["quality"] == 0.0


async def test_score_run_judge_disabled_propagates() -> None:
    called = []

    async def fake_judge(run, rubric) -> JudgeResult:
        called.append(1)
        return JudgeResult(model="m", success=1.0, quality=1.0)

    scored = await score_run(_run(), judge_fn=fake_judge, judge_disabled=True)
    assert called == []
    assert scored.skipped_judge


async def test_score_run_judge_error_keeps_heuristic_components() -> None:
    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", error="boom", cost_usd=0.0001)

    scored = await score_run(_run(), judge_fn=fake_judge)
    # Heuristics gave success=1.0 (clean run); judge failure leaves it.
    assert scored.components["success"] == 1.0
    # Quality not provided by failed judge → 0.0
    assert scored.components["quality"] == 0.0
    assert scored.judge.get("error") == "boom"


# ── ScoreSession end-to-end paths ────────────────────────────────────


def _write_trace(path: Path, trace_id: str, *, tokens: int = 50) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"event": "run_start", "trace_id": trace_id, "session_id": "s",
         "agent": "test-agent", "user_input": "hi",
         "timestamp": "2026-05-20T10:00:00+00:00"},
        {"event": "llm_response", "trace_id": trace_id, "session_id": "s",
         "response_text": "hi back",
         "usage": {"completion_tokens": tokens, "total_tokens": tokens}},
        {"event": "run_end", "trace_id": trace_id, "session_id": "s",
         "timestamp": "2026-05-20T10:00:01+00:00",
         "duration_ms": 1000, "llm_calls": 1, "tool_calls": 0,
         "total_tokens": tokens},
    ]
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


async def test_score_session_writes_scored_jsonl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    _write_trace(agent_dir / "2026-05-20.jsonl", "trace-a")
    _write_trace(agent_dir / "2026-05-20.jsonl", "trace-a")  # overwrites — same trace_id

    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.0002)

    monkeypatch.chdir(tmp_path)
    session = ScoreSession(judge_fn=fake_judge, rubric_resolver=lambda _: DEFAULT_RUBRIC)
    report = await session.run()

    assert report.scored_count == 1
    scored_path = agent_dir / "scored.jsonl"
    assert scored_path.is_file()
    loaded = load_scored_index(scored_path)
    assert "trace-a" in loaded


async def test_score_session_idempotent_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")

    judge_calls = []

    async def fake_judge(run, rubric) -> JudgeResult:
        judge_calls.append(1)
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.0)

    monkeypatch.chdir(tmp_path)
    s = ScoreSession(judge_fn=fake_judge, rubric_resolver=lambda _: DEFAULT_RUBRIC)
    await s.run()
    assert len(judge_calls) == 1

    # Second invocation should detect the existing scored row and skip the judge.
    report = await s.run()
    assert report.scored_count == 0
    assert report.skipped_existing == 1
    assert len(judge_calls) == 1  # no new judge calls


async def test_score_session_force_rescores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    calls = []

    async def fake_judge(run, rubric) -> JudgeResult:
        calls.append(1)
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.0)

    monkeypatch.chdir(tmp_path)
    await ScoreSession(judge_fn=fake_judge, rubric_resolver=lambda _: DEFAULT_RUBRIC).run()
    await ScoreSession(
        judge_fn=fake_judge, force=True, rubric_resolver=lambda _: DEFAULT_RUBRIC
    ).run()
    assert len(calls) == 2


async def test_score_session_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")

    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.0)

    monkeypatch.chdir(tmp_path)
    report = await ScoreSession(
        judge_fn=fake_judge, dry_run=True, rubric_resolver=lambda _: DEFAULT_RUBRIC
    ).run()
    assert report.scored_count == 1
    assert not (agent_dir / "scored.jsonl").exists()


async def test_score_session_budget_exhausted_disables_subsequent_judges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    # Three traces; each judge call costs $0.10; budget is $0.15 — second call exhausts.
    for i in range(3):
        _write_trace(agent_dir / f"t{i}.jsonl", f"tid-{i}")

    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.10)

    monkeypatch.chdir(tmp_path)
    report = await ScoreSession(
        judge_fn=fake_judge,
        max_cost_usd=0.15,
        concurrency=1,  # serial so budget check is deterministic
        rubric_resolver=lambda _: DEFAULT_RUBRIC,
    ).run()
    assert report.budget_exhausted
    # All three runs get scored (heuristics always run); but only some go through judge.
    assert report.scored_count == 3
    assert report.skipped_judge >= 1  # at least one heuristic-only after budget exhaust


async def test_score_session_version_bump_triggers_rescore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "generated-agents" / "demo-agent" / "traces"
    _write_trace(agent_dir / "t.jsonl", "tid-1")
    scored_path = agent_dir / "scored.jsonl"

    # Pre-seed scored.jsonl with a stale version.
    stale = ScoredRun(
        trace_id="tid-1",
        agent="demo-agent",
        scored_at="2026-01-01T00:00:00+00:00",
        scorer_version="0.0-old",
        rubric_version="r",
        overall=0.5,
        components={"success": 0.5},
    )
    scored_path.parent.mkdir(parents=True, exist_ok=True)
    from nuvel.eval.writer import append_scored
    append_scored(scored_path, stale)

    async def fake_judge(run, rubric) -> JudgeResult:
        return JudgeResult(model="m", success=1.0, quality=0.9, cost_usd=0.0)

    monkeypatch.chdir(tmp_path)
    report = await ScoreSession(
        judge_fn=fake_judge, rubric_resolver=lambda _: DEFAULT_RUBRIC
    ).run()
    # Stale row → rescored (not skipped as existing).
    assert report.scored_count == 1
    assert report.skipped_existing == 0
