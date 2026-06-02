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
