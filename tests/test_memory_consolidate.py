"""Tests for the periodic memory consolidation ("dream") pass in generated agents.

Exercises ``memory/consolidation`` — the store-agnostic dedupe / reconcile /
profile-build core plus the opt-in gate and graceful no-op degradation — and
``memory/profile`` (the structured ``## User Profile`` block loaded back into
the session tier). The template modules use relative imports, so they are
loaded by scaffolding a throwaway agent and importing its package (same
approach as ``test_memory_preload.py``).
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from nuvel.backends.adk.scaffold import scaffold_agent as adk_scaffold

TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "nuvel" / "backends" / "adk" / "templates" / "{{agent_package}}"
)


@pytest.fixture(scope="module")
def mods():
    """Scaffold an agent and import its consolidation + profile modules."""
    tmpdir = tempfile.mkdtemp()
    result = adk_scaffold("cons-tpl", output_dir=tmpdir)
    assert result["status"] == "ok"
    agent_dir = Path(result["path"])
    sys.path.insert(0, str(agent_dir))
    try:
        consolidation = importlib.import_module("cons_tpl.memory.consolidation")
        profile = importlib.import_module("cons_tpl.memory.profile")
        yield consolidation, profile
    finally:
        for mod in list(sys.modules):
            if mod == "cons_tpl" or mod.startswith("cons_tpl."):
                del sys.modules[mod]
        sys.path.remove(str(agent_dir))
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── exact-text dedup ─────────────────────────────────────────────────


def test_dedupe_exact_removes_duplicates(mods):
    consolidation, _ = mods
    entries = [
        "User prefers dark mode.",
        "user prefers dark mode.",       # case + trailing whitespace variant
        "  User   prefers  dark   mode. ",  # collapsed whitespace variant
        "Deploys to Railway.",
    ]
    kept = consolidation.dedupe_exact(entries)
    assert kept == ["User prefers dark mode.", "Deploys to Railway."]


async def test_consolidate_dedupes_exact_via_full_pass(mods):
    consolidation, _ = mods
    result = await consolidation.consolidate_memories(
        ["Fact A", "fact a", "Fact B"]
    )
    assert result["stats"]["input"] == 3
    assert result["stats"]["removed_exact"] == 1
    assert result["kept"] == ["Fact A", "Fact B"]


# ── contradiction reconciliation ─────────────────────────────────────


def test_reconcile_keeps_newer_and_flags_conflict(mods):
    consolidation, _ = mods
    # Oldest-first: the later "light mode" contradicts the earlier "dark mode".
    entries = [
        "User prefers dark mode",
        "User lives in Lisbon",
        "User prefers light mode",
    ]
    kept, conflicts = consolidation.reconcile_contradictions(entries)
    # Newer statement wins; the stale one is gone.
    assert "User prefers light mode" in kept
    assert "User prefers dark mode" not in kept
    assert "User lives in Lisbon" in kept  # unrelated topic untouched
    # Conflict is flagged with topic + both sides.
    assert len(conflicts) == 1
    assert conflicts[0]["topic"] == "user prefers"
    assert conflicts[0]["kept"] == "User prefers light mode"
    assert conflicts[0]["dropped"] == "User prefers dark mode"


def test_reconcile_no_false_positive_across_topics(mods):
    consolidation, _ = mods
    kept, conflicts = consolidation.reconcile_contradictions(
        ["User prefers tea", "User works remotely"]
    )
    assert conflicts == []
    assert kept == ["User prefers tea", "User works remotely"]


async def test_consolidate_full_pass_surfaces_conflicts(mods):
    consolidation, _ = mods
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return '{"summary": "s", "durable_facts": ["role is engineer"]}'

    result = await consolidation.consolidate_memories(
        ["User role is engineer", "User role is manager"],
        llm_fn=fake_llm,
    )
    assert result["stats"]["conflicts"] == 1
    # The resolved-contradictions note is threaded into the LLM prompt.
    assert "Resolved contradictions" in captured["prompt"]


# ── profile build shape ──────────────────────────────────────────────


async def test_profile_build_produces_expected_shape(mods):
    consolidation, _ = mods

    def fake_llm(_prompt):
        return (
            '{"summary": "A backend engineer who likes Rust.", '
            '"role": "Engineer", "interests": ["Rust", "databases"], '
            '"durable_facts": ["Based in Lisbon", "Prefers dark mode"]}'
        )

    result = await consolidation.consolidate_memories(
        ["Likes Rust", "Based in Lisbon"], llm_fn=fake_llm
    )
    prof = result["profile"]
    assert set(prof) == {"summary", "role", "interests", "durable_facts"}
    assert prof["role"] == "Engineer"
    assert prof["interests"] == ["Rust", "databases"]
    assert prof["durable_facts"] == ["Based in Lisbon", "Prefers dark mode"]


async def test_profile_build_handles_markdown_fenced_json(mods):
    consolidation, _ = mods

    def fake_llm(_prompt):
        return '```json\n{"summary": "hi", "role": "Dev"}\n```'

    result = await consolidation.consolidate_memories(
        ["some fact"], llm_fn=fake_llm
    )
    assert result["profile"]["summary"] == "hi"
    assert result["profile"]["role"] == "Dev"


def test_render_profile_block_shape(mods):
    _, profile = mods
    block = profile.render_profile_block(
        {
            "summary": "Backend engineer.",
            "role": "Engineer",
            "interests": ["Rust"],
            "durable_facts": ["Based in Lisbon"],
        }
    )
    assert block.startswith("## User Profile")
    assert "**Role:** Engineer" in block
    assert "**Interests:** Rust" in block
    assert "Based in Lisbon" in block


def test_render_profile_block_empty_is_blank(mods):
    _, profile = mods
    assert profile.render_profile_block({}) == ""


def test_profile_roundtrip_via_memory_dir(mods, tmp_path, monkeypatch):
    _, profile = mods
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path))
    profile.save_user_profile(
        {"summary": "s", "role": "Dev", "interests": ["x"]}, user_id="u1"
    )
    loaded = profile.load_user_profile("u1")
    assert loaded["role"] == "Dev"
    # Distinct users are isolated.
    assert profile.load_user_profile("other") == {}


# ── graceful no-op without an LLM / DB ───────────────────────────────


async def test_no_llm_still_dedupes_but_no_profile(mods):
    consolidation, _ = mods
    result = await consolidation.consolidate_memories(["A", "a", "B"])
    assert result["profile"] == {}  # nothing generated without an LLM
    assert result["kept"] == ["A", "B"]  # but dedupe still ran


async def test_run_job_empty_entries_is_noop(mods):
    consolidation, _ = mods
    result = await consolidation.run_consolidation_job(entries=[])
    assert result["profile"] == {}
    assert result["kept"] == []


async def test_run_job_without_profile_does_not_write(mods, tmp_path, monkeypatch):
    consolidation, profile = mods
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path))
    # No llm_fn → no profile → nothing persisted.
    await consolidation.run_consolidation_job(
        entries=["A", "B"], user_id="nobody"
    )
    assert profile.load_user_profile("nobody") == {}


async def test_run_job_persists_profile_when_generated(mods, tmp_path, monkeypatch):
    consolidation, profile = mods
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path))

    def fake_llm(_prompt):
        return '{"summary": "hi", "role": "Dev"}'

    await consolidation.run_consolidation_job(
        entries=["fact one", "fact two"], user_id="u9", llm_fn=fake_llm
    )
    assert profile.load_user_profile("u9")["role"] == "Dev"


# ── opt-in gate + scheduler ──────────────────────────────────────────


def test_opt_in_gate_default_off(mods, monkeypatch):
    consolidation, _ = mods
    monkeypatch.delenv(consolidation.ENV_ENABLED, raising=False)
    assert consolidation.is_enabled() is False


def test_opt_in_gate_respects_env(mods, monkeypatch):
    consolidation, _ = mods
    monkeypatch.setenv(consolidation.ENV_ENABLED, "1")
    assert consolidation.is_enabled() is True
    monkeypatch.setenv(consolidation.ENV_ENABLED, "0")
    assert consolidation.is_enabled() is False


def test_scheduler_start_is_noop_when_disabled(mods, monkeypatch):
    consolidation, _ = mods
    monkeypatch.delenv(consolidation.ENV_ENABLED, raising=False)
    consolidation.start_consolidation_scheduler()
    assert consolidation._HANDLE.task is None


async def test_scheduler_starts_when_enabled(mods, monkeypatch):
    consolidation, _ = mods
    monkeypatch.setenv(consolidation.ENV_ENABLED, "1")
    monkeypatch.setenv(consolidation.ENV_INTERVAL, "3600")
    ran = []
    consolidation.start_consolidation_scheduler(lambda: ran.append(1) or _noop())
    assert consolidation._HANDLE.task is not None
    await consolidation.stop_consolidation_scheduler()
    assert consolidation._HANDLE.task is None


async def _noop():
    return None


# ── cosine similarity dedup ──────────────────────────────────────────


def test_cosine_similarity_basic(mods):
    consolidation, _ = mods
    assert consolidation.cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert consolidation.cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert consolidation.cosine_similarity([], [1]) == 0.0


async def test_consolidate_collapses_near_duplicates_via_embeddings(mods):
    consolidation, _ = mods
    # "cats"/"felines" map to near-identical vectors → collapse to the longer.
    vecs = {
        "I love cats": [1.0, 0.0],
        "I love felines very much": [0.99, 0.01],
        "I code in Rust": [0.0, 1.0],
    }
    result = await consolidation.consolidate_memories(
        list(vecs),
        embed_fn=lambda t: vecs[t],
        threshold=0.9,
    )
    assert "I love felines very much" in result["kept"]  # longer/more specific won
    assert "I love cats" not in result["kept"]
    assert result["stats"]["removed_similar"] == 1


# ── template wiring ──────────────────────────────────────────────────


def test_consolidation_wired_into_lifespan():
    run_adk = (TEMPLATE_ROOT.parent / "run_adk.py").read_text()
    assert "start_consolidation_scheduler" in run_adk
    assert "stop_consolidation_scheduler" in run_adk


def test_profile_block_wired_into_session_tier():
    instr = (TEMPLATE_ROOT / "prompt" / "instructions.py.tmpl").read_text()
    assert "load_user_profile_block" in instr
