"""
Unit tests for the deck-style memory tools.

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
    import ppt_king.state.memory as mem_mod
    importlib.reload(mem_mod)
    import ppt_king.tools.style_tools as style_mod
    importlib.reload(style_mod)
    yield style_mod


def test_recall_empty(isolated_memory):
    out = isolated_memory.recall_deck_style()
    assert out["status"] == "empty"


def test_learn_then_recall(isolated_memory):
    out = isolated_memory.learn_style_from_kept_slide(
        title="Q3 revenue beat plan by 8%",
        bullets=["Revenue up 8%", "Margin held", "Pipeline grew"],
        notes="Open with the headline. Skip the variance table.",
        layout_name="Title and Content",
    )
    assert out["status"] == "ok"

    recalled = isolated_memory.recall_deck_style()
    assert recalled["status"] == "ok"
    assert "Q3 revenue" in recalled["style"]
    assert "Title and Content" in recalled["style"]


def test_consolidate_replaces_content(isolated_memory):
    isolated_memory.learn_style_from_kept_slide(
        title="One",
        bullets=["a", "b"],
    )
    isolated_memory.consolidate_deck_style(
        "# Deck style\n- 4 bullets max.\n- Statement titles.\n"
    )
    recalled = isolated_memory.recall_deck_style()
    assert "# Deck style" in recalled["style"]
    assert "Kept slide" not in recalled["style"]


def test_consolidate_rejects_empty(isolated_memory):
    out = isolated_memory.consolidate_deck_style("   ")
    assert out["status"] == "error"


def test_learn_skips_empty_slide(isolated_memory):
    out = isolated_memory.learn_style_from_kept_slide(title="", bullets=[], notes="")
    assert out["status"] == "skip"


def test_fingerprint_records_metrics(isolated_memory):
    # Same slide twice — the topic should accumulate two fingerprints.
    for _ in range(2):
        isolated_memory.learn_style_from_kept_slide(
            title="A title with five words here",
            bullets=["one two three", "four five six seven", "eight nine ten"],
            notes="Some notes carrying the narration.",
            layout_name="Title and Content",
        )
    recalled = isolated_memory.recall_deck_style()
    assert recalled["style"].count("## Kept slide") == 2
    # The avg bullet length should be present.
    assert "avg" in recalled["style"]
