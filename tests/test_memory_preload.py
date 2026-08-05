"""Tests for relevance-conditioned memory preload in generated agents.

Exercises ``memory/preload.retrieve_memory_block`` end-to-end across both
backends — the ADK ``memory_service`` (DB) path and the markdown fallback —
plus payload capping, the empty-memory no-crash case, and the template wiring
that hangs the preload off the agent's InstructionProvider.

The template modules live under ``{{agent_package}}/`` and use relative
imports, so they are loaded by scaffolding a throwaway agent and importing its
package (same approach as ``test_memory_review_fork.py``).
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
def preload_mod():
    """Scaffold an agent and import its ``memory.preload`` module."""
    tmpdir = tempfile.mkdtemp()
    result = adk_scaffold("prel-tpl", output_dir=tmpdir)
    assert result["status"] == "ok"
    agent_dir = Path(result["path"])
    sys.path.insert(0, str(agent_dir))
    try:
        yield importlib.import_module("prel_tpl.memory.preload")
    finally:
        for mod in list(sys.modules):
            if mod == "prel_tpl" or mod.startswith("prel_tpl."):
                del sys.modules[mod]
        sys.path.remove(str(agent_dir))
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── helpers to fake an ADK invocation context ────────────────────────


def _ctx(query: str, *, memory_service=None):
    parts = [SimpleNamespace(text=query)] if query else []
    return SimpleNamespace(
        user_content=SimpleNamespace(parts=parts),
        _invocation_context=SimpleNamespace(
            memory_service=memory_service,
            app_name="app",
            user_id="user",
            session=SimpleNamespace(events=[]),
        ),
    )


class _FakeMemoryService:
    """Minimal ADK ``BaseMemoryService`` stand-in returning canned memories."""

    def __init__(self, texts):
        self._texts = texts
        self.last_query = None

    async def search_memory(self, *, app_name, user_id, query):
        self.last_query = query
        memories = [
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=t)]))
            for t in self._texts
        ]
        return SimpleNamespace(memories=memories)


# ── DB backend path ──────────────────────────────────────────────────


async def test_preload_uses_memory_service_when_configured(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")
    service = _FakeMemoryService(
        ["Deploys to Railway on push to main.", "User prefers dark mode."]
    )
    # If the service answers, markdown must not be consulted at all.
    monkeypatch.setattr(
        preload_mod, "load_all_memory",
        lambda: (_ for _ in ()).throw(AssertionError("markdown should not be read")),
    )
    block = await preload_mod.retrieve_memory_block(
        _ctx("what deploy target", memory_service=service)
    )
    assert "Railway" in block
    assert service.last_query == "what deploy target"


async def test_preload_service_result_is_capped(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")
    monkeypatch.setenv(preload_mod.ENV_TOP_K, "3")
    service = _FakeMemoryService([f"memory chunk number {i}" for i in range(20)])
    block = await preload_mod.retrieve_memory_block(
        _ctx("chunk", memory_service=service)
    )
    # Bounded: no more than top_k chunks survive into the prompt.
    assert len([c for c in block.split("\n\n") if c.strip()]) <= 3


# ── markdown fallback path ───────────────────────────────────────────


async def test_preload_falls_back_to_markdown_without_db(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")
    blob = (
        "The user prefers dark mode in the editor.\n\n"
        "The project deploys to Railway on push to main.\n\n"
        "---\n\n"
        "User's timezone is Europe/Lisbon."
    )
    monkeypatch.setattr(preload_mod, "load_all_memory", lambda: blob)
    # No memory_service on the ctx → markdown ranking kicks in.
    block = await preload_mod.retrieve_memory_block(_ctx("deploy target railway"))
    assert "Railway" in block
    assert "dark mode" not in block  # irrelevant chunk excluded


async def test_preload_markdown_capped(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")
    monkeypatch.setenv(preload_mod.ENV_TOP_K, "2")
    blob = "\n\n".join(f"fact number {i} about widgets" for i in range(30))
    monkeypatch.setattr(preload_mod, "load_all_memory", lambda: blob)
    block = await preload_mod.retrieve_memory_block(_ctx("widgets fact"))
    assert len([c for c in block.split("\n\n") if c.strip()]) <= 2


async def test_preload_empty_memory_no_crash(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")
    monkeypatch.setattr(preload_mod, "load_all_memory", lambda: "")
    block = await preload_mod.retrieve_memory_block(_ctx("anything"))
    assert block == ""


async def test_preload_disabled_returns_whole_file(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "0")
    monkeypatch.setattr(preload_mod, "load_all_memory", lambda: "WHOLE FILE BLOB")
    # Disabled → legacy whole-file behaviour, no ranking.
    block = await preload_mod.retrieve_memory_block(_ctx("q"))
    assert block == "WHOLE FILE BLOB"


async def test_preload_service_error_falls_back_to_markdown(preload_mod, monkeypatch):
    monkeypatch.setenv(preload_mod.ENV_PRELOAD, "1")

    class _Boom:
        async def search_memory(self, **_):
            raise RuntimeError("db down")

    monkeypatch.setattr(preload_mod, "load_all_memory", lambda: "backup fact here")
    block = await preload_mod.retrieve_memory_block(
        _ctx("fact", memory_service=_Boom())
    )
    # Service failure must not surface; markdown covers it.
    assert "backup fact" in block


# ── template wiring ──────────────────────────────────────────────────


def test_preload_wired_into_instruction_provider():
    """The InstructionProvider must retrieve the relevance block per turn."""
    instr = (TEMPLATE_ROOT / "prompt" / "instructions.py.tmpl").read_text()
    assert "from ..memory.preload import retrieve_memory_block" in instr
    assert "await retrieve_memory_block(ctx)" in instr


def test_instruction_provider_wired_into_agent():
    """agent.py.tmpl must hand that InstructionProvider to the LlmAgent."""
    agent = (TEMPLATE_ROOT / "agent.py.tmpl").read_text()
    assert "from .prompt.instructions import get_agent_instruction" in agent
    assert "instruction=get_agent_instruction" in agent


def test_org_backend_default_when_db_configured():
    """Harness must build OrgMemoryService as the default backend from env."""
    harness = (TEMPLATE_ROOT / "harness.py.tmpl").read_text()
    assert "build_memory_service" in harness
