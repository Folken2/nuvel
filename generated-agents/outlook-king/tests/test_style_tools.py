"""
Unit tests for the writing-style memory tools.

Uses a tmp MEMORY_DIR so each test sees a clean slate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DIR", str(tmp_path))
    # Reimport to pick up env change in path helpers.
    import importlib
    import outlook_king.state.memory as mem_mod
    importlib.reload(mem_mod)
    import outlook_king.tools.style_tools as style_mod
    importlib.reload(style_mod)
    yield style_mod


def test_recall_empty(isolated_memory):
    out = isolated_memory.recall_writing_style()
    assert out["status"] == "empty"


def test_learn_then_recall(isolated_memory):
    body = "Hi Anna,\n\nThe report's attached. Let me know.\n\nThanks, J"
    out = isolated_memory.learn_style_from_sent_email(body, recipient="anna@x", subject="Report")
    assert out["status"] == "ok"

    recalled = isolated_memory.recall_writing_style()
    assert recalled["status"] == "ok"
    assert "anna@x" in recalled["style"]
    assert "Hi Anna" in recalled["style"]


def test_consolidate_replaces_content(isolated_memory):
    isolated_memory.learn_style_from_sent_email("Quick test.", recipient="x@x")
    isolated_memory.consolidate_writing_style("# Voice\n- Direct.\n- Plain.\n")
    recalled = isolated_memory.recall_writing_style()
    assert "# Voice" in recalled["style"]
    assert "Sent sample" not in recalled["style"]


def test_consolidate_rejects_empty(isolated_memory):
    out = isolated_memory.consolidate_writing_style("   ")
    assert out["status"] == "error"


def test_learn_skips_empty_body(isolated_memory):
    out = isolated_memory.learn_style_from_sent_email("", recipient="x@x")
    assert out["status"] == "skip"
