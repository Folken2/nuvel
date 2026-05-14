"""
Unit tests for the ppt-king context tools.

The tools read from ``tool_context.state``. We don't need a real ADK
ToolContext — a duck-typed object with a ``state`` dict matches the
contract these tools actually exercise.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ppt_king.tools import ppt_context as pc


class _FakeCtx:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {}


def test_get_current_slide_no_state():
    res = pc.get_current_slide(_FakeCtx())
    assert res["status"] == "no_slide"


def test_get_current_slide_passthrough():
    payload = {
        "index": 2,
        "slide_id": "s2",
        "title": "Roadmap",
        "bullets": ["Q1", "Q2"],
        "notes": "narration",
        "layout_name": "Title and Content",
        "shape_count": 3,
        "selected_shapes": [],
    }
    ctx = _FakeCtx({pc.CURRENT_SLIDE_KEY: payload})
    res = pc.get_current_slide(ctx)
    assert res["status"] == "ok"
    assert res["index"] == 2
    assert res["title"] == "Roadmap"
    assert res["shape_count"] == 3


def test_get_selected_shape_no_state():
    res = pc.get_selected_shape(_FakeCtx())
    assert res["status"] == "no_shape"


def test_get_selected_shape_returns_primary_and_all():
    shapes = [
        {"name": "Title 1", "type": "Placeholder", "text": "Hello",
         "left": 1.0, "top": 2.0, "width": 3.0, "height": 4.0,
         "is_placeholder": True},
        {"name": "TextBox 2", "type": "TextBox", "text": "Sub",
         "left": 5.0, "top": 6.0, "width": 7.0, "height": 8.0,
         "is_placeholder": False},
    ]
    ctx = _FakeCtx({pc.CURRENT_SLIDE_KEY: {"selected_shapes": shapes}})
    res = pc.get_selected_shape(ctx)
    assert res["status"] == "ok"
    assert res["count"] == 2
    assert res["primary"]["name"] == "Title 1"
    assert res["all"] == shapes


def test_get_deck_outline_no_state():
    assert pc.get_deck_outline(_FakeCtx())["status"] == "no_deck"


def test_get_recent_edits_empty_default():
    res = pc.get_recent_edits(_FakeCtx())
    assert res == {"status": "ok", "edits": []}


def test_get_recent_edits_returns_list():
    edits = [{"action": "apply_slide", "slide_index": 1, "summary": "x", "timestamp": "t"}]
    ctx = _FakeCtx({pc.RECENT_EDITS_KEY: edits})
    res = pc.get_recent_edits(ctx)
    assert res["edits"] == edits


def test_request_context_refresh_enqueues():
    ctx = _FakeCtx()
    res = pc.request_context_refresh(ctx)
    assert res["status"] == "queued"
    queue = ctx.state[pc.PENDING_ACTIONS_KEY]
    assert queue == [{"type": "request_refresh"}]
