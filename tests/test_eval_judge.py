"""LLM judge with the litellm boundary mocked.

We never make real network calls in tests; ``judge_run`` exposes a
``_call`` injection seam to swap the litellm adapter for a stub.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nuvel.eval.judge import _build_prompt, judge_run
from nuvel.eval.rubric import Rubric
from nuvel.traces_cli import Run


def _run(events: list[dict] | None = None, **kw: Any) -> Run:
    base = dict(
        agent="test",
        file=Path("/tmp/x"),
        session_id="s",
        trace_id="t",
        user_input="Help me write a memo",
        completion_tokens=10,
        events=events or [
            {"event": "run_start", "user_input": "Help me write a memo"},
            {"event": "llm_response", "response_text": "Sure, here it is."},
            {"event": "run_end"},
        ],
        schema="adk",
        ended_at="2026-05-20T00:00:00+00:00",
    )
    base.update(kw)
    return Run(**base)


def test_build_prompt_includes_user_input_and_assistant_text() -> None:
    run = _run()
    prompt = _build_prompt(run, Rubric())
    assert "Help me write a memo" in prompt
    assert "Sure, here it is." in prompt
    assert '"did_solve"' in prompt
    assert '"quality"' in prompt


def test_build_prompt_concats_multiple_llm_responses() -> None:
    run = _run(events=[
        {"event": "run_start", "user_input": "x"},
        {"event": "llm_response", "response_text": "first"},
        {"event": "llm_response", "response_text": "second"},
        {"event": "run_end"},
    ])
    prompt = _build_prompt(run, Rubric())
    assert "first" in prompt and "second" in prompt


def test_build_prompt_truncates_long_assistant_text() -> None:
    long = "x" * 20000
    run = _run(events=[
        {"event": "run_start"},
        {"event": "llm_response", "response_text": long},
        {"event": "run_end"},
    ])
    prompt = _build_prompt(run, Rubric())
    assert "[…]" in prompt
    assert len(prompt) < 20000  # truncated


def test_build_prompt_includes_extra_criteria() -> None:
    run = _run()
    r = Rubric(extra_criteria="Specifically check formality.")
    assert "Specifically check formality." in _build_prompt(run, r)


def test_build_prompt_lists_tool_calls() -> None:
    run = _run(events=[
        {"event": "run_start"},
        {"event": "tool_start", "tool": "search"},
        {"event": "tool_end", "tool": "search", "status": "ok"},
        {"event": "tool_start", "tool": "send"},
        {"event": "tool_end", "tool": "send", "status": "error"},
        {"event": "llm_response", "response_text": "done"},
        {"event": "run_end"},
    ])
    prompt = _build_prompt(run, Rubric())
    assert "search [ok]" in prompt
    assert "send [error]" in prompt


async def test_judge_run_happy_path() -> None:
    async def fake_call(model: str, prompt: str) -> tuple[str, float]:
        return (json.dumps({
            "did_solve": 0.9,
            "quality": 0.8,
            "efficiency_note": "fine",
            "notes": "solved cleanly",
        }), 0.0003)
    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert res.ok
    assert res.success == 0.9
    assert res.quality == 0.8
    assert res.cost_usd == 0.0003
    assert "solved cleanly" in res.notes


async def test_judge_run_clamps_out_of_range_scores() -> None:
    async def fake_call(m: str, p: str) -> tuple[str, float]:
        return (json.dumps({"did_solve": 1.5, "quality": -0.2}), 0.0)
    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert res.success == 1.0
    assert res.quality == 0.0


async def test_judge_run_extracts_json_from_prose() -> None:
    async def fake_call(m: str, p: str) -> tuple[str, float]:
        wrapper = 'Here is my evaluation:\n```json\n{"did_solve": 0.7, "quality": 0.6}\n```'
        return (wrapper, 0.0001)
    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert res.ok
    assert res.success == 0.7


async def test_judge_run_retries_on_parse_failure() -> None:
    attempts = []

    async def fake_call(m: str, p: str) -> tuple[str, float]:
        attempts.append(1)
        if len(attempts) == 1:
            return ("not json at all", 0.0001)
        return (json.dumps({"did_solve": 0.5, "quality": 0.5}), 0.0001)

    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert res.ok
    assert len(attempts) == 2
    # Cost accumulates across attempts.
    assert res.cost_usd == pytest.approx(0.0002)


async def test_judge_run_fails_after_two_attempts() -> None:
    async def fake_call(m: str, p: str) -> tuple[str, float]:
        return ("never json", 0.0001)
    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert not res.ok
    assert "json" in res.error
    assert res.cost_usd == pytest.approx(0.0002)


async def test_judge_run_handles_exception() -> None:
    async def fake_call(m: str, p: str) -> tuple[str, float]:
        raise RuntimeError("upstream 502")
    res = await judge_run(_run(), Rubric(judge_model="x"), _call=fake_call)
    assert not res.ok
    assert "RuntimeError" in res.error


async def test_judge_run_uses_rubric_resolved_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "should-not-win")
    seen: list[str] = []

    async def fake_call(m: str, p: str) -> tuple[str, float]:
        seen.append(m)
        return (json.dumps({"did_solve": 1, "quality": 1}), 0.0)

    await judge_run(_run(), Rubric(judge_model="rubric-override"), _call=fake_call)
    assert seen == ["rubric-override"]


# ── _call_litellm adapter (real litellm module, mocked acompletion) ──


def _fake_response(content: str):
    """Tiny fake litellm response object that satisfies _call_litellm's accessors."""
    class _Msg:
        pass
    class _Choice:
        message = _Msg()
    class _Resp:
        choices = [_Choice()]
    _Resp.choices[0].message.content = content
    return _Resp()


async def test_call_litellm_uses_response_format_when_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter requests JSON output; uses it directly when the model honors it."""
    import litellm

    from nuvel.eval.judge import _call_litellm

    captured: list[dict] = []

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return _fake_response('{"did_solve": 1, "quality": 1}')

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "completion_cost", lambda **_: 0.0001)

    content, cost = await _call_litellm("good-model", "the prompt")
    assert content == '{"did_solve": 1, "quality": 1}'
    assert cost == pytest.approx(0.0001)
    # Only the first call goes out when content is non-empty.
    assert len(captured) == 1
    assert captured[0]["response_format"] == {"type": "json_object"}


async def test_call_litellm_falls_back_when_response_format_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some providers (Kimi via OpenRouter) return empty content with response_format; retry without."""
    import litellm

    from nuvel.eval.judge import _call_litellm

    captured: list[dict] = []
    contents = iter(["", '{"did_solve": 0.8, "quality": 0.7}'])

    async def fake_acompletion(**kwargs):
        captured.append(kwargs)
        return _fake_response(next(contents))

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "completion_cost", lambda **_: 0.0002)

    content, cost = await _call_litellm("kimi-like", "p")
    assert content == '{"did_solve": 0.8, "quality": 0.7}'
    # Costs sum across both attempts.
    assert cost == pytest.approx(0.0004)
    assert len(captured) == 2
    # First call: with response_format.
    assert captured[0]["response_format"] == {"type": "json_object"}
    # Second call: without.
    assert "response_format" not in captured[1]


async def test_call_litellm_cost_failure_warns_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When litellm can't price the model, log a warn — but only once per process per model."""
    import litellm

    from nuvel.eval.judge import _UNPRICED_MODELS, _call_litellm

    class _FakeMessage:
        content = "{}"

    class _FakeChoice:
        message = _FakeMessage()

    class _FakeResponse:
        choices = [_FakeChoice()]

    async def fake_acompletion(**_):
        return _FakeResponse()

    def fake_cost(**_):
        raise ValueError("unknown model")

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "completion_cost", fake_cost)
    # Reset the module-level cache so this test is isolated.
    _UNPRICED_MODELS.discard("mystery-model")

    caplog.set_level("WARNING", logger="nuvel.eval.judge")
    await _call_litellm("mystery-model", "p")
    await _call_litellm("mystery-model", "p")
    await _call_litellm("mystery-model", "p")

    cost_warnings = [r for r in caplog.records if "cost lookup failed" in r.message]
    assert len(cost_warnings) == 1
    assert "mystery-model" in cost_warnings[0].message


async def test_judge_run_explicit_model_overrides_rubric() -> None:
    seen: list[str] = []

    async def fake_call(m: str, p: str) -> tuple[str, float]:
        seen.append(m)
        return (json.dumps({"did_solve": 1, "quality": 1}), 0.0)

    await judge_run(_run(), Rubric(judge_model="rubric-x"), model="explicit-y", _call=fake_call)
    assert seen == ["explicit-y"]
