"""Tests for the halt latch and the no-progress / repeated-failure guards."""

from types import SimpleNamespace

import pytest
from google.genai.types import Content, Part

from nuvel.guardrails.halt_consumer import (
    HALT_HANDOFF_DELIVERED_STATE_KEY,
    HALT_REASON_STATE_KEY,
    acknowledge_halt,
    halt_consumer_callback,
    halt_content,
    latch_halt,
    reset_halt_handoff,
)
from nuvel.guardrails.no_progress import NoProgressGuard
from nuvel.guardrails.repeated_failure import (
    LAST_ERROR_STATE_KEY,
    RepeatedFailureGuard,
)


def _ctx(state=None):
    return SimpleNamespace(state=state if state is not None else {})


def _text_response(text):
    return SimpleNamespace(content=Content(role="model", parts=[Part(text=text)]))


# ── halt latch primitives ─────────────────────────────────────────────

def test_latch_halt_sets_once():
    state = {}
    assert latch_halt(state, "first") is True
    assert state[HALT_REASON_STATE_KEY] == "first"
    # A second guard can't overwrite an existing latch.
    assert latch_halt(state, "second") is False
    assert state[HALT_REASON_STATE_KEY] == "first"


def test_acknowledge_and_reset_handoff_clear_state():
    state = {HALT_REASON_STATE_KEY: "x", HALT_HANDOFF_DELIVERED_STATE_KEY: True}
    acknowledge_halt(state)
    reset_halt_handoff(state)
    assert state[HALT_REASON_STATE_KEY] is None
    assert state[HALT_HANDOFF_DELIVERED_STATE_KEY] is None


def test_halt_content_envelope():
    content = halt_content("stuck")
    assert content.role == "model"
    assert content.parts[0].text == "[halted: stuck]"


async def test_halt_consumer_passthrough_when_not_latched():
    result = await halt_consumer_callback(callback_context=_ctx(), llm_request=None)
    assert result is None


async def test_halt_consumer_short_circuits_when_latched():
    state = {HALT_REASON_STATE_KEY: "loop detected"}
    ctx = _ctx(state)
    result = await halt_consumer_callback(callback_context=ctx, llm_request=None)
    assert result is not None
    assert result.content.parts[0].text == "[halted: loop detected]"
    assert state[HALT_HANDOFF_DELIVERED_STATE_KEY] is True


# ── NoProgressGuard ───────────────────────────────────────────────────

def test_no_progress_rejects_window_below_two():
    with pytest.raises(ValueError):
        NoProgressGuard(window=1)


async def test_no_progress_latches_after_identical_streak():
    guard = NoProgressGuard(window=3)
    ctx = _ctx()
    for _ in range(2):
        await guard.after_model_callback(
            callback_context=ctx, llm_response=_text_response("same answer")
        )
        assert ctx.state.get(HALT_REASON_STATE_KEY) is None
    # Third identical response reaches the window and latches.
    await guard.after_model_callback(
        callback_context=ctx, llm_response=_text_response("same answer")
    )
    assert "no progress" in ctx.state[HALT_REASON_STATE_KEY]


async def test_no_progress_resets_streak_on_change():
    guard = NoProgressGuard(window=2)
    ctx = _ctx()
    await guard.after_model_callback(
        callback_context=ctx, llm_response=_text_response("a")
    )
    await guard.after_model_callback(
        callback_context=ctx, llm_response=_text_response("b")
    )
    assert ctx.state.get(HALT_REASON_STATE_KEY) is None


async def test_no_progress_ignores_empty_text():
    guard = NoProgressGuard(window=2)
    ctx = _ctx()
    empty = SimpleNamespace(content=Content(role="model", parts=[]))
    for _ in range(5):
        await guard.after_model_callback(callback_context=ctx, llm_response=empty)
    assert ctx.state.get(HALT_REASON_STATE_KEY) is None


def test_no_progress_reset_clears_streak():
    state = {"__no_progress_streak__": 9, "__no_progress_last_text__": "x"}
    NoProgressGuard.reset(state)
    assert state["__no_progress_streak__"] == 0
    assert state["__no_progress_last_text__"] is None


# ── RepeatedFailureGuard ──────────────────────────────────────────────

def test_repeated_failure_rejects_threshold_below_two():
    with pytest.raises(ValueError):
        RepeatedFailureGuard(threshold=1)


async def _run_failure(guard, ctx, args, response):
    await guard.after_tool_callback(
        tool=SimpleNamespace(name="terminal"),
        args=args,
        tool_response=response,
        tool_context=ctx,
    )


async def test_repeated_failure_latches_on_identical_signature():
    guard = RepeatedFailureGuard(threshold=3)
    ctx = _ctx()
    args = {"command": "make build"}
    err = {"error": "boom"}
    for _ in range(2):
        await _run_failure(guard, ctx, args, err)
        assert ctx.state.get(HALT_REASON_STATE_KEY) is None
    await _run_failure(guard, ctx, args, err)
    assert "3 times in a row" in ctx.state[HALT_REASON_STATE_KEY]
    assert ctx.state[LAST_ERROR_STATE_KEY] == "boom"


async def test_repeated_failure_different_args_do_not_accumulate():
    guard = RepeatedFailureGuard(threshold=2)
    ctx = _ctx()
    err = {"error": "boom"}
    await _run_failure(guard, ctx, {"command": "a"}, err)
    await _run_failure(guard, ctx, {"command": "b"}, err)
    assert ctx.state.get(HALT_REASON_STATE_KEY) is None


async def test_repeated_failure_success_clears_streak_and_error():
    guard = RepeatedFailureGuard(threshold=2)
    ctx = _ctx()
    args = {"command": "make build"}
    await _run_failure(guard, ctx, args, {"error": "boom"})
    # A success on the same signature resets the streak.
    await _run_failure(guard, ctx, args, {"success": True})
    assert ctx.state[LAST_ERROR_STATE_KEY] is None
    await _run_failure(guard, ctx, args, {"error": "boom"})
    assert ctx.state.get(HALT_REASON_STATE_KEY) is None


async def test_repeated_failure_exit_code_counts_as_failure():
    guard = RepeatedFailureGuard(threshold=2)
    ctx = _ctx()
    args = {"command": "x"}
    resp = {"exit_code": 2, "stderr": "nope"}
    await _run_failure(guard, ctx, args, resp)
    await _run_failure(guard, ctx, args, resp)
    assert ctx.state.get(HALT_REASON_STATE_KEY) is not None
    assert "exit_code=2" in ctx.state[LAST_ERROR_STATE_KEY]


async def test_repeated_failure_non_mapping_response_ignored():
    guard = RepeatedFailureGuard(threshold=2)
    ctx = _ctx()
    args = {"command": "x"}
    await _run_failure(guard, ctx, args, "just a string")
    await _run_failure(guard, ctx, args, "just a string")
    assert ctx.state.get(HALT_REASON_STATE_KEY) is None


def test_repeated_failure_reset_clears_streaks():
    state = {"__repeated_failure_streak__": {"sig": {"count": 3}}}
    RepeatedFailureGuard.reset(state)
    assert state["__repeated_failure_streak__"] is None
