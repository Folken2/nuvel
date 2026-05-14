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
    import word_king.state.memory as mem_mod
    importlib.reload(mem_mod)
    import word_king.tools.style_tools as style_mod
    importlib.reload(style_mod)
    yield style_mod


def test_recall_empty(isolated_memory):
    out = isolated_memory.recall_writing_style()
    assert out["status"] == "empty"


def test_learn_then_recall(isolated_memory):
    passage = (
        "The rollout begins on Monday. We have prepared the rollback "
        "plan and the success metrics.\n\n"
        "Therefore, the team can proceed with confidence."
    )
    out = isolated_memory.learn_style_from_passage(
        passage, source="accepted-draft", note="release note"
    )
    assert out["status"] == "ok"

    recalled = isolated_memory.recall_writing_style()
    assert recalled["status"] == "ok"
    assert "accepted-draft" in recalled["style"]
    assert "release note" in recalled["style"]
    # Fingerprint should mention paragraphs and sentences.
    assert "paragraphs" in recalled["style"]


def test_learn_skips_empty_passage(isolated_memory):
    out = isolated_memory.learn_style_from_passage("", source="user-pasted")
    assert out["status"] == "skip"


def test_consolidate_replaces_content(isolated_memory):
    isolated_memory.learn_style_from_passage(
        "Quick test passage.", source="user-pasted"
    )
    isolated_memory.consolidate_writing_style(
        "# Voice\n- Direct.\n- Plain.\n- Short paragraphs.\n"
    )
    recalled = isolated_memory.recall_writing_style()
    assert recalled["status"] == "ok"
    assert "# Voice" in recalled["style"]
    # Raw fingerprint block should be gone.
    assert "Passage sample" not in recalled["style"]


def test_consolidate_rejects_empty(isolated_memory):
    out = isolated_memory.consolidate_writing_style("   ")
    assert out["status"] == "error"


def test_fingerprint_captures_formal_markers(isolated_memory):
    passage = (
        "Therefore the board concluded the matter. Furthermore, the "
        "committee shall reconvene next quarter. Henceforth, all "
        "submissions are due by the 5th."
    )
    out = isolated_memory.learn_style_from_passage(
        passage, source="accepted-draft", note="board memo"
    )
    assert out["status"] == "ok"
    recalled = isolated_memory.recall_writing_style()
    # Formal markers should be reported in the fingerprint text.
    assert "formal markers" in recalled["style"]


def test_fingerprint_captures_bullets(isolated_memory):
    passage = (
        "Action items:\n"
        "- finalize the budget\n"
        "- circulate the deck\n"
        "- book the room\n"
    )
    out = isolated_memory.learn_style_from_passage(
        passage, source="accepted-draft"
    )
    assert out["status"] == "ok"
    recalled = isolated_memory.recall_writing_style()
    assert "bullet" in recalled["style"]


def test_full_loop(isolated_memory):
    # recall-empty → learn → recall → consolidate → recall
    assert isolated_memory.recall_writing_style()["status"] == "empty"
    isolated_memory.learn_style_from_passage(
        "Sample one. Two sentences here.", source="accepted-draft"
    )
    after_learn = isolated_memory.recall_writing_style()
    assert after_learn["status"] == "ok"

    isolated_memory.consolidate_writing_style(
        "# Voice\n- Short and direct.\n- Mid-formal register.\n"
    )
    after_cons = isolated_memory.recall_writing_style()
    assert after_cons["status"] == "ok"
    assert "Short and direct" in after_cons["style"]
    assert "Sample one" not in after_cons["style"]
