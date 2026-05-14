"""
Unit tests for ppt-king action tools.

These tools don't touch PowerPoint directly — they enqueue JSON action
dicts into ``tool_context.state[PENDING_ACTIONS_KEY]``. The taskpane
drains and executes them via Office.js. We assert the queue contents
match the contract the addin executor expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ppt_king.tools import action_tools as at
from ppt_king.tools.ppt_context import PENDING_ACTIONS_KEY


class _FakeCtx:
    """Minimal ToolContext-shaped fake — only ``state`` is touched."""

    def __init__(self) -> None:
        self.state: dict = {}


def test_apply_slide_queues_action():
    ctx = _FakeCtx()
    res = at.queue_apply_slide(ctx, 2, "T", ["a", "b"], "n")
    assert res["status"] == "queued"
    queue = ctx.state[PENDING_ACTIONS_KEY]
    assert len(queue) == 1
    assert queue[0] == {
        "type": "apply_slide",
        "slide_index": 2,
        "title": "T",
        "bullets": ["a", "b"],
        "notes": "n",
    }


def test_multiple_actions_accumulate():
    ctx = _FakeCtx()
    at.queue_apply_slide(ctx, 0, "T1", ["a"])
    at.queue_insert_slide(ctx, 0, "T2", ["b"])
    at.queue_move_slide(ctx, 4, 1)
    queue = ctx.state[PENDING_ACTIONS_KEY]
    assert [a["type"] for a in queue] == ["apply_slide", "insert_slide", "move_slide"]


def test_replace_text_validates_args():
    ctx = _FakeCtx()
    err = at.queue_replace_text(ctx, "", "foo")
    assert err["status"] == "error"
    err2 = at.queue_replace_text(ctx, "Acme", "Globex", scope="bogus")
    assert err2["status"] == "error"
    assert PENDING_ACTIONS_KEY not in ctx.state or ctx.state[PENDING_ACTIONS_KEY] == []


def test_replace_text_happy_path():
    ctx = _FakeCtx()
    res = at.queue_replace_text(ctx, "Acme", "Globex", scope="deck")
    assert res["status"] == "queued"
    a = ctx.state[PENDING_ACTIONS_KEY][0]
    assert a["type"] == "replace_text"
    assert a["find"] == "Acme"
    assert a["replace"] == "Globex"
    assert a["scope"] == "deck"
    assert a["match_case"] is False


def test_set_shape_text_carries_name():
    ctx = _FakeCtx()
    at.queue_set_shape_text(ctx, 3, "Title 1", "Hello")
    a = ctx.state[PENDING_ACTIONS_KEY][0]
    assert a == {
        "type": "set_shape_text",
        "slide_index": 3,
        "shape_name": "Title 1",
        "text": "Hello",
    }


def test_add_text_box_defaults():
    ctx = _FakeCtx()
    at.queue_add_text_box(ctx, 0, "label")
    a = ctx.state[PENDING_ACTIONS_KEY][0]
    assert a["type"] == "add_text_box"
    assert a["slide_index"] == 0
    assert a["left"] == 50.0
    assert a["top"] == 50.0
    assert a["width"] == 400.0
    assert a["height"] == 80.0


def test_delete_and_duplicate_payloads():
    ctx = _FakeCtx()
    at.queue_duplicate_slide(ctx, 5)
    at.queue_delete_slide(ctx, 7)
    types = [a["type"] for a in ctx.state[PENDING_ACTIONS_KEY]]
    assert types == ["duplicate_slide", "delete_slide"]
    assert ctx.state[PENDING_ACTIONS_KEY][0]["slide_index"] == 5
    assert ctx.state[PENDING_ACTIONS_KEY][1]["slide_index"] == 7


def test_set_notes_payload():
    ctx = _FakeCtx()
    at.queue_set_notes(ctx, 1, "speaker note")
    a = ctx.state[PENDING_ACTIONS_KEY][0]
    assert a == {"type": "set_notes", "slide_index": 1, "notes": "speaker note"}
