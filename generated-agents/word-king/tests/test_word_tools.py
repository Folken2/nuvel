"""
Unit tests for the pure-Python Word tools.

These tools are stateless heuristics (no ADK runner, no LLM, no
external services). The goal is to lock down the structural-analysis
behavior so the agent's prompts can rely on stable shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from word_king.tools.draft_tools import propose_section_outline, rewrite_passage_hints


# ── propose_section_outline ────────────────────────────────────────


def test_outline_empty_brief():
    out = propose_section_outline("")
    assert out["status"] == "error"


def test_outline_whitespace_only():
    out = propose_section_outline("   \n\t  ")
    assert out["status"] == "error"


def test_outline_returns_at_least_three():
    out = propose_section_outline(
        "Write a section about how we handle incident response.",
        target_word_count=600,
    )
    assert out["status"] == "ok"
    assert len(out["headings"]) >= 3
    for h in out["headings"]:
        assert h["heading"]
        assert h["scope"]
        assert h["target_words"] > 0


def test_outline_respects_bulleted_beats():
    brief = (
        "Cover the rollout plan:\n"
        "- timeline\n"
        "- risks\n"
        "- success metrics\n"
        "- rollback plan\n"
    )
    out = propose_section_outline(brief, target_word_count=800)
    assert out["status"] == "ok"
    headings = [h["heading"].lower() for h in out["headings"]]
    # Beats should be discoverable in the heading set.
    assert any("timeline" in h for h in headings)
    assert any("risk" in h for h in headings)


def test_outline_caps_to_six():
    brief = "\n".join(f"- beat {i}" for i in range(20))
    out = propose_section_outline(brief)
    assert out["status"] == "ok"
    assert len(out["headings"]) <= 6


def test_outline_invalid_target():
    out = propose_section_outline("Some brief", target_word_count=0)
    assert out["status"] == "error"


# ── rewrite_passage_hints ──────────────────────────────────────────


def test_hints_empty():
    out = rewrite_passage_hints("", "tighten this")
    assert out["status"] == "empty"


def test_hints_basic_counts():
    text = "The report is ready. I will send it tomorrow."
    out = rewrite_passage_hints(text, "make it tighter")
    assert out["status"] == "ok"
    assert out["word_count"] == 9
    assert out["sentence_count"] == 2
    assert out["classified_ask"] == "shorten"


def test_hints_counts_hedges():
    text = (
        "I just kind of think maybe we should perhaps reconsider. "
        "Arguably the rollout is somewhat risky."
    )
    out = rewrite_passage_hints(text, "rewrite this")
    assert out["hedge_count"] >= 5
    assert "maybe" in out["hedges_found"] or any("kind of" in h for h in out["hedges_found"])


def test_hints_finds_longest_sentence():
    short = "Short."
    long = " ".join(["word"] * 25) + "."
    text = short + " " + long
    out = rewrite_passage_hints(text, "clarity")
    assert out["longest_sentence_words"] >= 25
    # Index should point to the longer (second) sentence.
    assert out["longest_sentence_index"] == 1


def test_hints_counts_passive_voice():
    text = "The decision was made yesterday. The report was reviewed by the team."
    out = rewrite_passage_hints(text, "fix passive")
    assert out["passive_voice_approx"] >= 2


def test_hints_classifies_minimal_fix():
    out = rewrite_passage_hints("This has a typo somwhere.", "fix the typo")
    assert out["classified_ask"] == "minimal-fix"


def test_hints_classifies_expand():
    out = rewrite_passage_hints("Short.", "expand on this please")
    assert out["classified_ask"] == "expand"
    assert out["target_word_count_high"] > out["word_count"]


def test_hints_classifies_shorten():
    text = " ".join(["word"] * 50) + "."
    out = rewrite_passage_hints(text, "shorten this")
    assert out["classified_ask"] == "shorten"
    assert out["target_word_count_high"] < out["word_count"]


def test_hints_classifies_raise_register():
    out = rewrite_passage_hints("yo, what's up", "make this more formal")
    assert out["classified_ask"] == "raise-register"


def test_hints_preserves_quotes():
    text = 'He said "the deadline is firm" yesterday.'
    out = rewrite_passage_hints(text, "tighten")
    assert out["quoted_spans"] >= 1
    assert any("the deadline is firm" in q for q in out["quoted_examples"])


def test_hints_default_window_within_20_percent():
    # 100-word passage with neutral ask should give a window centered around 100.
    text = " ".join(["word"] * 100) + "."
    out = rewrite_passage_hints(text, "rewrite this")
    assert out["target_word_count_low"] >= 80
    assert out["target_word_count_high"] <= 120
