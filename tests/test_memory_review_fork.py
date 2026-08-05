"""Tests for the long-horizon memory self-improvement layer.

Covers both the meta-agent mirror (``nuvel.memory``) — throttle, sibling-runner
drain, judge-fork recursion guard — and the generated-agent template modules
(``preload`` chunk shape, ``org_backend`` fallback), imported from a scaffolded
package.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold


# ── meta-agent mirror: throttle ──────────────────────────────────────

from nuvel.memory import throttle as mirror_throttle


def test_throttle_cooldown_blocks_second_claim(monkeypatch):
    monkeypatch.setenv(mirror_throttle.ENV_COOLDOWN, "120")
    monkeypatch.setenv(mirror_throttle.ENV_CAP, "50")
    state: dict = {}
    assert mirror_throttle.try_claim(state, "review", now=1000.0) is True
    # Within the cooldown window: refused, state unchanged.
    assert mirror_throttle.try_claim(state, "review", now=1030.0) is False
    # After the window elapses: allowed again.
    assert mirror_throttle.try_claim(state, "review", now=1200.0) is True


def test_throttle_per_session_cap(monkeypatch):
    monkeypatch.setenv(mirror_throttle.ENV_COOLDOWN, "0")  # disable cooldown
    monkeypatch.setenv(mirror_throttle.ENV_CAP, "3")
    state: dict = {}
    assert [mirror_throttle.try_claim(state, "review") for _ in range(5)] == [
        True, True, True, False, False,
    ]


def test_throttle_none_state_always_claims():
    assert mirror_throttle.try_claim(None, "review") is True


def test_throttle_is_per_fork_type(monkeypatch):
    monkeypatch.setenv(mirror_throttle.ENV_COOLDOWN, "120")
    monkeypatch.delenv(mirror_throttle.ENV_CAP, raising=False)
    state: dict = {}
    assert mirror_throttle.try_claim(state, "review", now=1000.0) is True
    # A different fork type has its own cooldown budget.
    assert mirror_throttle.try_claim(state, "consolidate", now=1000.0) is True


# ── meta-agent mirror: recursion guard ───────────────────────────────


def test_judge_agent_has_no_after_agent_chain():
    """Structural recursion guard: the judge can never spawn a judge."""
    from nuvel.memory import review_fork

    judge = review_fork._build_judge_agent()
    assert not getattr(judge, "after_agent_callback", None)
    # Whitelisted to memory-write + skill-read only.
    tool_names = {getattr(t, "name", None) for t in judge.tools}
    assert tool_names == set(review_fork.REVIEW_TOOL_NAMES)


async def test_review_fork_disabled_by_default(monkeypatch):
    """Opt-in: with the env unset the callback is a no-op (no spawn)."""
    from nuvel.memory import review_fork

    monkeypatch.delenv(review_fork.REVIEW_FORK_ENABLED_ENV, raising=False)
    spawned = []
    monkeypatch.setattr(
        review_fork.SIBLING_RUNNER, "spawn",
        lambda **kw: spawned.append(kw),
    )
    ctx = SimpleNamespace(state={}, _invocation_context=SimpleNamespace())
    await review_fork.review_fork_callback(ctx)
    assert spawned == []


# ── meta-agent mirror: sibling-runner drain ──────────────────────────

from nuvel.memory import sibling_runner as mirror_sibling


class _SleepRunner:
    """Fake ADK Runner whose run drives longer than the drain budget."""

    def __init__(self, seconds: float):
        self._seconds = seconds

    async def run_async(self, **_kwargs):
        await asyncio.sleep(self._seconds)
        if False:  # pragma: no cover - make this an async generator
            yield None


async def test_sibling_runner_drain_times_out(monkeypatch):
    monkeypatch.setattr(
        mirror_sibling, "_build_runner",
        lambda **kw: _SleepRunner(5.0),
    )
    runner = mirror_sibling.SiblingRunner(drain_timeout=0.1)
    task = runner.spawn(
        agent=object(), prompt="x", app_name="a", user_id="u",
    )
    # close() must return within ~the drain budget, never hang on the slow run.
    await asyncio.wait_for(runner.close(), timeout=1.0)
    assert not task.done()  # dropped, not awaited to completion
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await task


async def test_sibling_runner_drain_awaits_fast_task(monkeypatch):
    completed = asyncio.Event()

    class _FastRunner:
        async def run_async(self, **_kwargs):
            completed.set()
            return
            yield  # pragma: no cover

    monkeypatch.setattr(mirror_sibling, "_build_runner", lambda **kw: _FastRunner())
    runner = mirror_sibling.SiblingRunner(drain_timeout=2.0)
    runner.spawn(agent=object(), prompt="x", app_name="a", user_id="u")
    await runner.close()
    assert completed.is_set()
    assert not runner._pending


def test_sibling_drain_timeout_capped_under_adk_budget(monkeypatch):
    monkeypatch.setenv(mirror_sibling.ENV_DRAIN_TIMEOUT, "99")
    runner = mirror_sibling.SiblingRunner()
    assert runner._drain_timeout <= mirror_sibling.MAX_DRAIN_TIMEOUT < 5.0


# ── generated-agent template: preload + org_backend ──────────────────


@pytest.fixture(scope="module")
def generated_pkg():
    """Scaffold an agent and import its package so template modules load."""
    tmpdir = tempfile.mkdtemp()
    result = adk_scaffold("mem-tpl", output_dir=tmpdir)
    assert result["status"] == "ok"
    agent_dir = Path(result["path"])
    sys.path.insert(0, str(agent_dir))
    try:
        import importlib

        preload = importlib.import_module("mem_tpl.memory.preload")
        org_backend = importlib.import_module("mem_tpl.memory.org_backend")
        yield SimpleNamespace(preload=preload, org_backend=org_backend)
    finally:
        for mod in list(sys.modules):
            if mod == "mem_tpl" or mod.startswith("mem_tpl."):
                del sys.modules[mod]
        sys.path.remove(str(agent_dir))
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_preload_rank_chunk_shape(generated_pkg):
    rank = generated_pkg.preload.rank_markdown_chunks
    blob = (
        "The user prefers dark mode in the editor.\n\n"
        "The project deploys to Railway on push to main.\n\n"
        "---\n\n"
        "User's timezone is Europe/Lisbon."
    )
    hits = rank("what deploy target does the project use", blob, top_k=2)
    # Shape: a ranked list of plain-text chunks, capped at top_k.
    assert isinstance(hits, list)
    assert 1 <= len(hits) <= 2
    assert all(isinstance(h, str) for h in hits)
    # The Railway/deploy chunk must rank first for a deploy-shaped query.
    assert "Railway" in hits[0]


def test_preload_rank_empty_blob(generated_pkg):
    assert generated_pkg.preload.rank_markdown_chunks("q", "", top_k=5) == []


def test_preload_rank_no_query_tokens_returns_prefix(generated_pkg):
    blob = "alpha fact.\n\nbeta fact.\n\ngamma fact."
    hits = generated_pkg.preload.rank_markdown_chunks("", blob, top_k=2)
    assert hits == ["alpha fact.", "beta fact."]


async def test_org_backend_fallback_when_no_dsn(generated_pkg, monkeypatch):
    ob = generated_pkg.org_backend
    monkeypatch.delenv(ob.ENV_DSN, raising=False)
    assert ob.org_memory_configured() is False
    # No DSN → None so the agent falls back to markdown memory.
    assert await ob.build_memory_service() is None


async def test_org_backend_fallback_when_pkg_missing(generated_pkg, monkeypatch):
    ob = generated_pkg.org_backend
    monkeypatch.setenv(ob.ENV_DSN, "postgresql://x/y")
    # Force the nuvel.memory import inside build_memory_service to fail so we
    # exercise the "DSN set but package unavailable" fallback branch.
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name.startswith("nuvel.memory"):
            raise ImportError("simulated missing extra")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert ob.org_memory_configured() is True
    assert await ob.build_memory_service() is None
