"""Tests for the 3-tier system prompt in generated agents.

The instruction builder (``prompt/instructions``) assembles the system prompt
as a stable tier (identity/persona — byte-identical across turns so the prefix
stays cache-hot), a session tier (slow-changing: user profile + retrieved
memory), and a volatile tier (per-turn reminders that ride the tail). These
tests pin the cache-stability contract: the stable prefix must not move when
only volatile content changes, and the full prompt must be the ordered
concatenation of the tiers.

The template modules use relative imports, so they are loaded by scaffolding a
throwaway agent and importing its package (same approach as
``test_memory_preload.py``).
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold

TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "nuvel" / "backends" / "adk" / "templates" / "{{agent_package}}"
)


@pytest.fixture(scope="module")
def instr_mod():
    """Scaffold an agent and import its ``prompt.instructions`` module."""
    tmpdir = tempfile.mkdtemp()
    result = adk_scaffold("tier-tpl", output_dir=tmpdir)
    assert result["status"] == "ok"
    agent_dir = Path(result["path"])
    sys.path.insert(0, str(agent_dir))
    try:
        yield importlib.import_module("tier_tpl.prompt.instructions")
    finally:
        for mod in list(sys.modules):
            if mod == "tier_tpl" or mod.startswith("tier_tpl."):
                del sys.modules[mod]
        sys.path.remove(str(agent_dir))
        shutil.rmtree(tmpdir, ignore_errors=True)


def _ctx(*, state=None, user_id="u", query="hello"):
    parts = [SimpleNamespace(text=query)] if query else []
    return SimpleNamespace(
        user_content=SimpleNamespace(parts=parts),
        _invocation_context=SimpleNamespace(
            user_id=user_id,
            app_name="app",
            memory_service=None,
            session=SimpleNamespace(state=state or {}, events=[]),
        ),
    )


# ── stable tier ──────────────────────────────────────────────────────


def test_stable_tier_identical_across_turns(instr_mod):
    """Turn-to-turn the stable prefix must be byte-identical (cache-hot)."""
    a = instr_mod.build_stable_tier()
    b = instr_mod.build_stable_tier()
    assert a == b
    assert a  # non-empty (carries the system frame at minimum)


def test_stable_tier_ignores_volatile_state(instr_mod):
    """Different volatile state must not perturb the stable tier."""
    _ = instr_mod.build_volatile_tier(_ctx(state={"_last_error": "boom"}))
    stable_after = instr_mod.build_stable_tier()
    assert stable_after == instr_mod.build_stable_tier()


# ── volatile tier ────────────────────────────────────────────────────


def test_volatile_tier_changes_per_turn(instr_mod):
    """Distinct per-turn reminders produce distinct volatile tails."""
    v1 = instr_mod.build_volatile_tier(_ctx(state={"_last_error": "err-1"}))
    v2 = instr_mod.build_volatile_tier(_ctx(state={"_last_error": "err-2"}))
    assert v1 != v2
    assert "err-1" in v1
    assert "err-2" in v2


def test_volatile_tier_surfaces_all_reminders(instr_mod):
    v = instr_mod.build_volatile_tier(
        _ctx(
            state={
                "_infra_warning": "db degraded",
                "_last_error": "timeout",
                "_near_budget": True,
            }
        )
    )
    assert "db degraded" in v
    assert "timeout" in v
    assert "budget" in v.lower()


def test_volatile_tier_minimal_when_no_state(instr_mod):
    v = instr_mod.build_volatile_tier(_ctx(state={}))
    # Always carries the date; nothing else with an empty state.
    assert "Today:" in v
    assert "[!]" not in v


# ── session tier ─────────────────────────────────────────────────────


async def test_session_tier_stable_when_memory_unchanged(instr_mod, monkeypatch):
    """The session tier only moves when profile/memory actually change."""
    monkeypatch.setattr(instr_mod, "load_user_profile_block", lambda uid: "## User Profile\nX")

    async def fake_mem(_ctx):
        return "fixed memory block"

    monkeypatch.setattr(instr_mod, "retrieve_memory_block", fake_mem)
    s1 = await instr_mod.build_session_tier(_ctx(query="alpha"))
    s2 = await instr_mod.build_session_tier(_ctx(query="beta"))
    assert s1 == s2  # unchanged sources → unchanged tier
    assert "User Profile" in s1
    assert "fixed memory block" in s1


async def test_session_tier_reflects_profile_change(instr_mod, monkeypatch):
    async def empty_mem(_ctx):
        return ""

    monkeypatch.setattr(instr_mod, "retrieve_memory_block", empty_mem)
    monkeypatch.setattr(instr_mod, "load_user_profile_block", lambda uid: "profile-v1")
    s1 = await instr_mod.build_session_tier(_ctx())
    monkeypatch.setattr(instr_mod, "load_user_profile_block", lambda uid: "profile-v2")
    s2 = await instr_mod.build_session_tier(_ctx())
    assert s1 != s2


async def test_session_tier_degrades_when_sources_fail(instr_mod, monkeypatch):
    """A failing memory source must not break prompt assembly."""
    def boom(_uid):
        raise RuntimeError("profile store down")

    async def boom_mem(_ctx):
        raise RuntimeError("mem store down")

    monkeypatch.setattr(instr_mod, "load_user_profile_block", boom)
    monkeypatch.setattr(instr_mod, "retrieve_memory_block", boom_mem)
    # Must not raise; empty session tier is acceptable.
    assert await instr_mod.build_session_tier(_ctx()) == ""


# ── full assembly ────────────────────────────────────────────────────


async def test_full_prompt_is_ordered_concatenation(instr_mod, monkeypatch):
    monkeypatch.setattr(instr_mod, "load_user_profile_block", lambda uid: "PROFILE")

    async def fake_mem(_ctx):
        return "MEMBLOCK"

    monkeypatch.setattr(instr_mod, "retrieve_memory_block", fake_mem)
    ctx = _ctx(state={"_last_error": "E9"})

    stable = instr_mod.build_stable_tier()
    session = await instr_mod.build_session_tier(ctx)
    volatile = instr_mod.build_volatile_tier(ctx)
    full = await instr_mod.get_agent_instruction(ctx)

    expected = "\n\n".join(t for t in (stable, session, volatile) if t)
    assert full == expected
    # Ordering: stable prefix first, volatile tail last.
    assert full.startswith(stable)
    assert full.rstrip().endswith(volatile.rstrip())
    assert full.index("PROFILE") < full.index("E9")


async def test_full_prompt_stable_prefix_survives_volatile_change(instr_mod, monkeypatch):
    """Changing only volatile state must leave the stable prefix byte-identical."""
    monkeypatch.setattr(instr_mod, "load_user_profile_block", lambda uid: "")

    async def empty_mem(_ctx):
        return ""

    monkeypatch.setattr(instr_mod, "retrieve_memory_block", empty_mem)
    stable = instr_mod.build_stable_tier()
    p1 = await instr_mod.get_agent_instruction(_ctx(state={"_last_error": "a"}))
    p2 = await instr_mod.get_agent_instruction(_ctx(state={"_last_error": "b"}))
    assert p1 != p2  # volatile differs
    assert p1.startswith(stable) and p2.startswith(stable)  # shared cache-hot prefix


# ── template wiring ──────────────────────────────────────────────────


def test_template_declares_three_tiers():
    src = (TEMPLATE_ROOT / "prompt" / "instructions.py.tmpl").read_text()
    for fn in ("build_stable_tier", "build_session_tier", "build_volatile_tier"):
        assert f"def {fn}" in src or f"async def {fn}" in src
    assert "def get_agent_instruction" in src or "async def get_agent_instruction" in src
