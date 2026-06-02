"""Per-trace replay: synthetic-run construction, scoring, retry, skips."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuvel.eval.replay.runner import build_synthetic_run, replay_run
from nuvel.eval.replay.schema import Variant
from nuvel.eval.schema import JudgeResult
from nuvel.traces_cli import Run


def _src_run(user_input: str | None = "Summarize my inbox") -> Run:
    return Run(
        agent="outlook-king",
        file=Path("/tmp/outlook-king/traces/2026-05-20.jsonl"),
        session_id="s1",
        trace_id="t1",
        user_input=user_input,
    )


def _variant() -> Variant:
    return Variant(version="v-1.0", name="friendlier", system_prompt="Be warm.", model="m/x")


async def _fake_judge(run, rubric) -> JudgeResult:
    # Asserts the synthetic run reaches the judge with the replayed output.
    assert run.user_input == "Summarize my inbox"
    return JudgeResult(model="judge/x", success=1.0, quality=0.9, cost_usd=0.0002)


def test_build_synthetic_run_is_judgeable() -> None:
    """The synthetic run MUST avoid heuristics' skip_judge early-exit:
    it needs a run_end event AND completion_tokens > 0."""
    from nuvel.eval.heuristics import apply_heuristics
    run = build_synthetic_run(_src_run(), "Here is your summary.")
    assert run.schema == "adk"
    assert run.completion_tokens > 0
    assert any(ev.get("event") == "run_end" for ev in run.events)
    res = apply_heuristics(run)
    assert res.skip_judge is False  # the whole point — judge must run


async def test_replay_run_produces_scored_result() -> None:
    async def fake_chat(model, system, user, *, temperature, max_tokens):
        assert system == "Be warm."
        assert user == "Summarize my inbox"
        assert model == "m/x"
        return ("Sure — 3 unread, all low priority.", 0.0004)

    result = await replay_run(
        _src_run(), _variant(), _call=fake_chat, judge_fn=_fake_judge,
    )
    assert result.trace_id == "t1"
    assert result.agent == "outlook-king"
    assert result.variant_version == "v-1.0"
    assert result.model == "m/x"
    assert result.output_text == "Sure — 3 unread, all low priority."
    assert result.replay_cost_usd == 0.0004
    assert result.scored["components"]["quality"] == 0.9
    assert result.scored["trace_id"] == "t1"


async def test_replay_run_retries_chat_once_then_succeeds() -> None:
    calls = {"n": 0}

    async def flaky_chat(model, system, user, *, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient 503")
        return ("ok", 0.0)

    result = await replay_run(_src_run(), _variant(), _call=flaky_chat, judge_fn=_fake_judge)
    assert calls["n"] == 2
    assert result.output_text == "ok"


async def test_replay_run_raises_after_second_chat_failure() -> None:
    async def dead_chat(model, system, user, *, temperature, max_tokens):
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        await replay_run(_src_run(), _variant(), _call=dead_chat, judge_fn=_fake_judge)


# --- append to tests/test_eval_replay_runner.py ---
import json

from nuvel.eval.replay.runner import ReplayRunner
from nuvel.eval.replay.schema import ReplayResult, load_replay_index


def _write_traces(traces_dir: Path, n: int = 3) -> None:
    """Write one ADK trace file with n complete runs carrying user_input."""
    traces_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(n):
        sid = f"s{i}"
        lines.append({"event": "run_start", "session_id": sid, "trace_id": f"t{i}",
                      "agent": traces_dir.parent.name, "user_input": f"question {i}"})
        lines.append({"event": "llm_response", "session_id": sid, "response_text": "orig"})
        lines.append({"event": "run_end", "session_id": sid})
    (traces_dir / "2026-05-20.jsonl").write_text(
        "\n".join(json.dumps(d) for d in lines) + "\n", encoding="utf-8")


def _runner(traces_dir: Path, **kw):
    async def fake_chat(model, system, user, *, temperature, max_tokens):
        return (f"variant reply to {user}", 0.0001)

    async def fake_judge(run, rubric):
        from nuvel.eval.schema import JudgeResult
        return JudgeResult(model="j", success=1.0, quality=0.8, cost_usd=0.0002)

    defaults = dict(
        variant=_variant(),
        traces_dir=traces_dir,
        agent=traces_dir.parent.name,
        chat_fn=fake_chat,
        judge_fn=fake_judge,
    )
    defaults.update(kw)
    return ReplayRunner(**defaults)


async def test_runner_writes_one_result_per_trace(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=3)
    report = await _runner(traces).run()
    assert report.replayed == 3
    idx = load_replay_index(traces / "replays" / "friendlier.jsonl")
    assert len(idx) == 3
    assert all(r.output_text.startswith("variant reply") for r in idx.values())


async def test_runner_is_idempotent_on_same_version(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    await _runner(traces).run()
    second = await _runner(traces).run()
    assert second.replayed == 0
    assert second.skipped_existing == 2


async def test_runner_force_rescore(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    await _runner(traces).run()
    forced = await _runner(traces, force=True).run()
    assert forced.replayed == 2


async def test_runner_skips_traces_without_user_input(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    traces.mkdir(parents=True)
    (traces / "2026-05-20.jsonl").write_text(json.dumps(
        {"event": "run_start", "session_id": "s0", "trace_id": "t0",
         "agent": "outlook-king"}) + "\n" + json.dumps(
        {"event": "run_end", "session_id": "s0"}) + "\n", encoding="utf-8")
    report = await _runner(traces).run()
    assert report.replayed == 0
    assert report.skipped_no_input == 1


async def test_runner_stops_at_cost_budget(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=10)
    # each trace = 0.0001 (chat) + 0.0002 (judge) = 0.0003; budget 0.0005 ⇒ ~2 traces
    report = await _runner(traces, max_cost_usd=0.0005).run()
    assert report.budget_exhausted is True
    assert report.replayed < 10


async def test_runner_dry_run_writes_nothing(tmp_path: Path) -> None:
    traces = tmp_path / "outlook-king" / "traces"
    _write_traces(traces, n=2)
    report = await _runner(traces, dry_run=True).run()
    assert report.replayed == 2
    assert not (traces / "replays" / "friendlier.jsonl").exists()
