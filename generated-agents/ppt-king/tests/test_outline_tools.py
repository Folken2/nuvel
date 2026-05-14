"""
Unit tests for the pure-Python deck outlining + bullet tools.

These tools are stateless heuristics (no ADK runner, no LLM). The goal
is to lock down the structural-analysis behavior so the agent's prompts
can rely on stable shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ppt_king.tools.outline_tools import plan_deck_outline, tighten_bullets_hints


# ── plan_deck_outline ──────────────────────────────────────────────


def test_plan_deck_outline_empty_brief_errors():
    out = plan_deck_outline("")
    assert out["status"] == "error"


def test_plan_deck_outline_returns_scaffold():
    out = plan_deck_outline("Series A pitch about our developer tools startup", 10)
    assert out["status"] == "ok"
    assert out["intent"] == "pitch"
    assert out["target_slide_count"] == 10
    # Ratios sum to the target.
    r = out["ratios"]
    assert r["intro"] + r["body"] + r["closing"] == 10
    # Sections list mirrors the ratios.
    section_total = sum(s["slide_count"] for s in out["sections"])
    assert section_total == 10
    assert any(s["role"] == "intro" for s in out["sections"])
    assert any(s["role"] == "closing" for s in out["sections"])
    assert isinstance(out["hints"], list) and len(out["hints"]) >= 1


def test_plan_deck_outline_detects_training():
    out = plan_deck_outline("60-minute training workshop on code review for new engineers", 12)
    assert out["intent"] == "training"


def test_plan_deck_outline_detects_report():
    out = plan_deck_outline("Annual report on platform reliability findings", 10)
    assert out["intent"] == "report"


def test_plan_deck_outline_detects_status():
    out = plan_deck_outline("Weekly status update for the integrations team", 6)
    assert out["intent"] == "status"


def test_plan_deck_outline_falls_back_to_general():
    out = plan_deck_outline("Talk about coffee", 5)
    assert out["intent"] == "general"


def test_plan_deck_outline_clamps_slide_count():
    out = plan_deck_outline("A pitch", 1)
    assert out["target_slide_count"] >= 3
    out2 = plan_deck_outline("A pitch", 999)
    assert out2["target_slide_count"] <= 60


def test_plan_deck_outline_long_decks_get_agenda_hint():
    out = plan_deck_outline("Long internal training on platform onboarding", 20)
    assert any("agenda" in h.lower() for h in out["hints"])


# ── tighten_bullets_hints ──────────────────────────────────────────


def test_tighten_bullets_hints_empty():
    out = tighten_bullets_hints([])
    assert out["status"] == "empty"


def test_tighten_bullets_hints_basic_counts():
    bullets = [
        "Cut latency by 40% on the read path",
        "Ship the v2 search index by Friday",
        "Reduce on-call volume to one incident per week",
    ]
    out = tighten_bullets_hints(bullets)
    assert out["status"] == "ok"
    assert out["count"] == 3
    for b in out["bullets"]:
        assert b["word_count"] > 0
    assert out["max_word_count"] >= 7


def test_tighten_bullets_hints_detects_long_bullet():
    bullets = ["A short one.", " ".join(["word"] * 14) + "."]
    out = tighten_bullets_hints(bullets)
    assert out["any_over_10_words"] is True
    assert out["max_word_count"] >= 14


def test_tighten_bullets_hints_parallelism_flag_true_when_all_verb_start():
    bullets = ["Ship feature A", "Cut cost B", "Build pipeline C"]
    out = tighten_bullets_hints(bullets)
    assert out["parallel_verb_starts"] is True


def test_tighten_bullets_hints_parallelism_flag_false_with_article_start():
    bullets = ["Ship feature A", "The cost goes down"]
    out = tighten_bullets_hints(bullets)
    assert out["parallel_verb_starts"] is False


def test_tighten_bullets_hints_detects_numbers():
    bullets = ["Cut latency 40%", "Hire team"]
    out = tighten_bullets_hints(bullets)
    assert out["bullets"][0]["has_number"] is True
    assert out["bullets"][1]["has_number"] is False


def test_tighten_bullets_hints_ends_with_period():
    bullets = ["A bullet.", "Another bullet"]
    out = tighten_bullets_hints(bullets)
    assert out["bullets"][0]["ends_with_period"] is True
    assert out["bullets"][1]["ends_with_period"] is False
