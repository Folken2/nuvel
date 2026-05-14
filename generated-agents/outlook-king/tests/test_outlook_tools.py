"""
Unit tests for the pure-Python Outlook tools.

These tools are stateless heuristics (no ADK runner, no LLM, no Composio
calls). The goal is to lock down the structural-analysis behavior so the
agent's prompts can rely on stable shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outlook_king.tools.coach_tools import analyze_draft
from outlook_king.tools.search_hints import plan_email_search, rank_search_hits


# ── analyze_draft ───────────────────────────────────────────────────


def test_analyze_draft_empty():
    out = analyze_draft("")
    assert out["status"] == "empty"


def test_analyze_draft_basic_counts():
    body = "Hi Anna,\n\nThe report is ready. I'll send it later today.\n\nThanks,\nJ"
    out = analyze_draft(body, recipients="anna@example.com")
    assert out["status"] == "ok"
    assert out["word_count"] > 0
    assert out["sentence_count"] >= 2
    assert out["has_opener"] is True
    assert out["has_signoff"] is True
    assert out["recipient_count"] == 1


def test_analyze_draft_flags_hedges_and_apologies():
    body = "Hi, I just wanted to maybe ask — sorry for the delay. I think we should perhaps reschedule."
    out = analyze_draft(body)
    assert out["hedge_count"] >= 3
    assert out["apology_count"] >= 1


def test_analyze_draft_long_sentence_detection():
    long_sentence = " ".join(["word"] * 35) + "."
    out = analyze_draft(long_sentence)
    assert out["long_sentences_over_30w"] >= 1
    assert out["longest_sentence_words"] >= 35


def test_analyze_draft_broadcast_flag():
    out = analyze_draft("Quick update.", recipients="a@x, b@x, c@x, d@x")
    assert out["recipient_count"] == 4
    assert out["is_broadcast"] is True


# ── plan_email_search ──────────────────────────────────────────────


def test_plan_email_search_extracts_address():
    out = plan_email_search("emails from anna@example.com about Q3 budget")
    assert out["status"] == "ok"
    assert "anna@example.com" in out["from_addresses"]
    assert any("budget" in k or "q3" in k for k in out["keywords"])


def test_plan_email_search_detects_relative_date():
    out = plan_email_search("messages from last week about onboarding")
    assert out["after_iso"] is not None


def test_plan_email_search_detects_attachments():
    out = plan_email_search("anything with a spreadsheet attached")
    assert out["has_attachments"] is True


def test_plan_email_search_empty():
    out = plan_email_search("")
    assert out["status"] == "error"


# ── rank_search_hits ───────────────────────────────────────────────


def test_rank_search_hits_recency_wins():
    import json

    hits = [
        {"from": "x@x", "received": "2020-01-01T00:00:00Z", "subject": "old"},
        {"from": "x@x", "received": "2099-01-01T00:00:00Z", "subject": "new"},
    ]
    out = rank_search_hits(json.dumps(hits))
    assert out["status"] == "ok"
    assert out["ranked"][0]["subject"] == "new"


def test_rank_search_hits_invalid_json():
    out = rank_search_hits("not json")
    assert out["status"] == "error"


def test_rank_search_hits_frequent_sender_boost():
    import json

    hits = [
        {"from": "rare@x", "received": "2024-01-01T00:00:00Z", "subject": "rare"},
        {"from": "frequent@x", "received": "2024-01-01T00:00:00Z", "subject": "f1"},
        {"from": "frequent@x", "received": "2024-01-01T00:00:00Z", "subject": "f2"},
        {"from": "frequent@x", "received": "2024-01-01T00:00:00Z", "subject": "f3"},
    ]
    out = rank_search_hits(json.dumps(hits))
    # Frequent sender should outrank rare for same date
    top_two = [h["from"] for h in out["ranked"][:2]]
    assert "frequent@x" in top_two
