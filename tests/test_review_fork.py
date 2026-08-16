"""Judge-fork self-improvement loop — behavioral guarantees.

These tests pin the contract of the after-turn "judge fork" against the
meta-agent mirror (``nuvel.memory``), which is importable without scaffolding.
The generated-agent template modules are byte-for-byte parallel, so the same
guarantees hold there; the scaffolded copy is exercised separately in
``tests/test_memory_review_fork.py``.

Guarantees covered here:

* throttle honors the cooldown window,
* throttle honors the per-session cap,
* the judge agent has an empty ``after_agent_callback`` chain (structural
  recursion guard),
* the callback is fire-and-forget — it returns immediately even when the
  spawned fork runs far longer than any turn, and
* a failure inside the fork never propagates to the parent.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from nuvel.memory import review_fork
from nuvel.memory import sibling_runner as sibling_mod
from nuvel.memory import throttle


# ── throttle: cooldown window ────────────────────────────────────────


def test_throttle_respects_cooldown(monkeypatch):
    monkeypatch.setenv(throttle.ENV_COOLDOWN, "120")
    monkeypatch.setenv(throttle.ENV_CAP, "50")
    state: dict = {}
    # First claim of the turn goes through.
    assert throttle.try_claim(state, "review", now=1_000.0) is True
    # A second claim inside the 120s window is refused, state untouched.
    assert throttle.try_claim(state, "review", now=1_030.0) is False
    # Once the window elapses the next claim is allowed again.
    assert throttle.try_claim(state, "review", now=1_121.0) is True


# ── throttle: per-session cap ────────────────────────────────────────


def test_throttle_respects_per_session_cap(monkeypatch):
    monkeypatch.setenv(throttle.ENV_COOLDOWN, "0")  # isolate the cap
    monkeypatch.setenv(throttle.ENV_CAP, "3")
    state: dict = {}
    outcomes = [throttle.try_claim(state, "review") for _ in range(5)]
    assert outcomes == [True, True, True, False, False]


# ── recursion guard ──────────────────────────────────────────────────


def test_judge_fork_has_empty_after_agent_chain():
    """Structural guard: a judge can never spawn a judge of a judge."""
    judge = review_fork._build_judge_agent()
    assert not getattr(judge, "after_agent_callback", None)
    # And its toolset is whitelisted to memory-write + skill-read only.
    tool_names = {getattr(t, "name", None) for t in judge.tools}
    assert tool_names == set(review_fork.REVIEW_TOOL_NAMES)


# ── fire-and-forget: parent never blocks ─────────────────────────────


class _SlowRunner:
    """Fake ADK Runner whose drive loop runs longer than any real turn."""

    async def run_async(self, **_kwargs):
        await asyncio.sleep(30.0)
        if False:  # pragma: no cover - make this an async generator
            yield None


async def test_callback_returns_immediately_even_if_fork_is_slow(monkeypatch):
    monkeypatch.setenv(review_fork.REVIEW_FORK_ENABLED_ENV, "1")
    monkeypatch.setenv(throttle.ENV_COOLDOWN, "0")
    # The spawned fork would take 30s; the callback must not wait on it.
    monkeypatch.setattr(sibling_mod, "_build_runner", lambda **kw: _SlowRunner())
    monkeypatch.setattr(review_fork, "_get_judge_agent", lambda: object())

    ictx = SimpleNamespace(
        session=SimpleNamespace(events=[]),
        app_name="app",
        user_id="user",
        memory_service=None,
    )
    ctx = SimpleNamespace(state={}, _invocation_context=ictx)

    start = time.monotonic()
    await review_fork.review_fork_callback(ctx)
    elapsed = time.monotonic() - start

    # Parent reply path is unblocked — well under the fork's 30s sleep.
    assert elapsed < 1.0
    # The fork is genuinely in flight (not silently skipped).
    pending = list(sibling_mod.SIBLING_RUNNER._pending)
    assert pending, "expected an in-flight judge task"
    for task in pending:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task


# ── error isolation ──────────────────────────────────────────────────


async def test_fork_failure_never_raises_to_parent(monkeypatch):
    monkeypatch.setenv(review_fork.REVIEW_FORK_ENABLED_ENV, "1")
    monkeypatch.setenv(throttle.ENV_COOLDOWN, "0")

    def _boom(**_kwargs):
        raise RuntimeError("spawn exploded")

    monkeypatch.setattr(review_fork.SIBLING_RUNNER, "spawn", _boom)

    ictx = SimpleNamespace(
        session=SimpleNamespace(events=[]),
        app_name="app",
        user_id="user",
        memory_service=None,
    )
    ctx = SimpleNamespace(state={}, _invocation_context=ictx)

    # The exception is caught and logged inside the callback — the parent's
    # after_agent chain completes normally.
    assert await review_fork.review_fork_callback(ctx) is None


async def test_fork_run_failure_is_swallowed(monkeypatch):
    """A failure *inside* the fork's drive loop is swallowed by the runner."""

    class _FailingRunner:
        async def run_async(self, **_kwargs):
            raise RuntimeError("model call failed")
            yield  # pragma: no cover

    monkeypatch.setattr(sibling_mod, "_build_runner", lambda **kw: _FailingRunner())
    runner = sibling_mod.SiblingRunner(drain_timeout=1.0)
    task = runner.spawn(agent=object(), prompt="x", app_name="a", user_id="u")
    # Draining an errored task must not re-raise; close() returns cleanly.
    await runner.close()
    assert task.done() and task.exception() is None
