"""
Unit tests for the Word action tools.

Actions enqueue structured payloads into ADK session state — the
add-in is the executor. These tests verify the queue contract: kind,
params validation, and that bad input refuses to enqueue.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from word_king.tools.word_actions import (
    insert_text,
    replace_selection,
    apply_formatting,
    insert_heading,
    insert_table,
    insert_comment,
    find_and_replace,
    navigate_to_heading,
    delete_selection,
    PENDING_ACTIONS_KEY,
)


class FakeToolContext:
    def __init__(self):
        self.state: dict = {}


def test_insert_text_queues():
    ctx = FakeToolContext()
    out = insert_text("hello", "selection", tool_context=ctx)
    assert out["status"] == "queued"
    assert out["kind"] == "insert_text"
    queue = ctx.state[PENDING_ACTIONS_KEY]
    assert len(queue) == 1
    assert queue[0]["kind"] == "insert_text"
    assert queue[0]["params"] == {"text": "hello", "location": "selection"}


def test_insert_text_rejects_empty():
    ctx = FakeToolContext()
    out = insert_text("", tool_context=ctx)
    assert out["status"] == "error"
    assert PENDING_ACTIONS_KEY not in ctx.state or not ctx.state[PENDING_ACTIONS_KEY]


def test_insert_text_rejects_bad_location():
    ctx = FakeToolContext()
    out = insert_text("hi", "moon", tool_context=ctx)
    assert out["status"] == "error"


def test_replace_selection():
    ctx = FakeToolContext()
    out = replace_selection("rewritten", tool_context=ctx)
    assert out["status"] == "queued"
    assert ctx.state[PENDING_ACTIONS_KEY][0]["params"]["text"] == "rewritten"


def test_apply_formatting_needs_params():
    ctx = FakeToolContext()
    out = apply_formatting(tool_context=ctx)
    assert out["status"] == "error"


def test_apply_formatting_bold():
    ctx = FakeToolContext()
    out = apply_formatting(bold=True, tool_context=ctx)
    assert out["status"] == "queued"
    assert ctx.state[PENDING_ACTIONS_KEY][0]["params"]["bold"] is True


def test_apply_formatting_style_validates():
    ctx = FakeToolContext()
    out = apply_formatting(style="Bogus", tool_context=ctx)
    assert out["status"] == "error"
    out2 = apply_formatting(style="Heading2", tool_context=ctx)
    assert out2["status"] == "queued"


def test_insert_heading_levels():
    ctx = FakeToolContext()
    assert insert_heading("Topic", level=1, tool_context=ctx)["status"] == "queued"
    assert insert_heading("Topic", level=7, tool_context=ctx)["status"] == "error"
    assert insert_heading("", level=2, tool_context=ctx)["status"] == "error"


def test_insert_table_validates_shape():
    ctx = FakeToolContext()
    assert insert_table([["a", "b"], ["c", "d"]], tool_context=ctx)["status"] == "queued"
    assert insert_table([["a", "b"], ["c"]], tool_context=ctx)["status"] == "error"
    assert insert_table([], tool_context=ctx)["status"] == "error"


def test_insert_comment():
    ctx = FakeToolContext()
    out = insert_comment("Tighten this sentence.", tool_context=ctx)
    assert out["status"] == "queued"
    assert ctx.state[PENDING_ACTIONS_KEY][0]["params"]["on"] == "selection"


def test_find_and_replace():
    ctx = FakeToolContext()
    out = find_and_replace("foo", "bar", match_case=True, tool_context=ctx)
    assert out["status"] == "queued"
    p = ctx.state[PENDING_ACTIONS_KEY][0]["params"]
    assert p["find"] == "foo" and p["replace"] == "bar" and p["match_case"] is True
    assert find_and_replace("", "bar", tool_context=ctx)["status"] == "error"


def test_navigate_to_heading():
    ctx = FakeToolContext()
    assert navigate_to_heading("Intro", tool_context=ctx)["status"] == "queued"
    assert navigate_to_heading("  ", tool_context=ctx)["status"] == "error"


def test_delete_selection():
    ctx = FakeToolContext()
    assert delete_selection(tool_context=ctx)["status"] == "queued"


def test_actions_accumulate_in_queue():
    ctx = FakeToolContext()
    insert_text("a", tool_context=ctx)
    insert_heading("Topic", level=2, tool_context=ctx)
    apply_formatting(bold=True, tool_context=ctx)
    assert len(ctx.state[PENDING_ACTIONS_KEY]) == 3
    kinds = [a["kind"] for a in ctx.state[PENDING_ACTIONS_KEY]]
    assert kinds == ["insert_text", "insert_heading", "apply_formatting"]
