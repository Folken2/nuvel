"""
Unit tests for the deck-structure analysis + reorder tools.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ppt_king.tools.structure_tools import analyze_deck_flow, suggest_reordering


# ── analyze_deck_flow ─────────────────────────────────────────────


def _outline(titles, bullet_counts=None, has_notes=None):
    bullet_counts = bullet_counts or [0] * len(titles)
    has_notes = has_notes or [False] * len(titles)
    return {
        "slide_count": len(titles),
        "slides": [
            {
                "index": i,
                "title": t,
                "bullet_count": bullet_counts[i],
                "has_notes": has_notes[i],
            }
            for i, t in enumerate(titles)
        ],
    }


def test_analyze_deck_flow_empty_outline_errors():
    out = analyze_deck_flow("")
    assert out["status"] == "error"


def test_analyze_deck_flow_detects_repeated_titles():
    outline = _outline(
        ["Title", "Results", "Results", "Results", "Conclusion"]
    )
    out = analyze_deck_flow(json.dumps(outline))
    assert out["status"] == "ok"
    kinds = [o["kind"] for o in out["observations"]]
    assert "repeated_titles" in kinds


def test_analyze_deck_flow_detects_missing_agenda_on_long_deck():
    titles = ["Title"] + [f"Body {i}" for i in range(10)]
    outline = _outline(titles)
    out = analyze_deck_flow(json.dumps(outline))
    kinds = [o["kind"] for o in out["observations"]]
    assert "missing_agenda" in kinds


def test_analyze_deck_flow_skips_agenda_warning_when_present():
    titles = ["Title", "Agenda"] + [f"Body {i}" for i in range(8)]
    outline = _outline(titles)
    out = analyze_deck_flow(json.dumps(outline))
    kinds = [o["kind"] for o in out["observations"]]
    assert "missing_agenda" not in kinds


def test_analyze_deck_flow_detects_missing_cta():
    titles = [
        "Title", "Problem", "Problem deeper", "Solution",
        "Solution detail", "Evidence", "Thanks",
    ]
    outline = _outline(titles)
    out = analyze_deck_flow(json.dumps(outline))
    kinds = [o["kind"] for o in out["observations"]]
    assert "missing_cta" in kinds


def test_analyze_deck_flow_detects_bullet_overload():
    titles = ["Title"] + [f"Body {i}" for i in range(5)]
    bullets = [0, 4, 4, 4, 4, 12]
    outline = _outline(titles, bullet_counts=bullets)
    out = analyze_deck_flow(json.dumps(outline))
    kinds = [o["kind"] for o in out["observations"]]
    assert "bullet_overload" in kinds


def test_analyze_deck_flow_accepts_dict_input():
    outline = _outline(["A", "A", "A", "B"])
    out = analyze_deck_flow(outline)  # not JSON-encoded
    assert out["status"] == "ok"


# ── suggest_reordering ────────────────────────────────────────────


def test_suggest_reordering_empty_errors():
    out = suggest_reordering("")
    assert out["status"] == "error"


def test_suggest_reordering_puts_cta_last():
    titles = [
        "Title", "Problem", "Solution", "Our ask",
        "Evidence", "More evidence", "Wrap-up",
    ]
    outline = _outline(titles)
    out = suggest_reordering(json.dumps(outline))
    assert out["status"] == "ok"
    moves = out["moves"]
    cta_move = next((m for m in moves if "ask" in m["reason"].lower()), None)
    assert cta_move is not None
    assert cta_move["to_index"] == len(titles) - 1


def test_suggest_reordering_promotes_agenda_to_index_one():
    titles = [
        "Title", "Body A", "Body B", "Agenda", "Body C",
        "Body D", "Body E", "Body F", "Body G", "Body H",
    ]
    outline = _outline(titles)
    out = suggest_reordering(json.dumps(outline))
    moves = out["moves"]
    agenda_move = next((m for m in moves if "agenda" in m["reason"].lower()), None)
    assert agenda_move is not None
    assert agenda_move["to_index"] == 1


def test_suggest_reordering_methodology_before_results():
    titles = [
        "Title", "TL;DR", "Results overview", "Results detail",
        "Methodology", "Implications",
    ]
    outline = _outline(titles)
    out = suggest_reordering(json.dumps(outline))
    moves = out["moves"]
    method_move = next((m for m in moves if "methodology" in m["reason"].lower()), None)
    assert method_move is not None
    assert method_move["from_index"] > method_move["to_index"]


def test_suggest_reordering_problem_before_solution():
    titles = ["Title", "Solution", "Solution detail", "Problem", "Evidence", "Our ask"]
    outline = _outline(titles)
    out = suggest_reordering(json.dumps(outline))
    moves = out["moves"]
    pb = next((m for m in moves if "problem" in m["reason"].lower()), None)
    assert pb is not None
    assert pb["from_index"] > pb["to_index"]


def test_suggest_reordering_no_moves_when_already_ordered():
    titles = [
        "Title", "Agenda", "Problem", "Solution",
        "Evidence", "Methodology", "Results", "Our ask",
    ]
    outline = _outline(titles)
    out = suggest_reordering(json.dumps(outline))
    # We allow zero or a couple — anything that's ordered shouldn't suggest
    # a CTA move, agenda move, or methodology/results swap.
    reasons = " ".join(m["reason"].lower() for m in out["moves"])
    assert "agenda" not in reasons
    assert "ask should be the closing" not in reasons
