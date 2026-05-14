"""
Tests for the action-recording tools.

These tools queue actions into ADK session state under
``outlook:pending_actions``. We exercise them with a minimal fake
ToolContext so we don't need an ADK runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outlook_king.tools.outlook_actions import (
    PENDING_ACTIONS_KEY,
    ACTION_RESULTS_KEY,
    insert_text_at_cursor,
    replace_compose_body,
    set_subject,
    add_recipients,
    remove_recipients,
    set_importance,
    attach_file_from_url,
    create_reply_draft,
    create_forward_draft,
    apply_categories,
    set_flag,
    refresh_outlook_context,
    get_recent_action_results,
)


class FakeCtx:
    def __init__(self, initial=None):
        self.state = dict(initial or {})


def _last_action(ctx: FakeCtx) -> dict:
    return ctx.state[PENDING_ACTIONS_KEY][-1]


def test_insert_text_queues_action():
    ctx = FakeCtx()
    out = insert_text_at_cursor(ctx, "Hello world")
    assert out["status"] == "queued"
    a = _last_action(ctx)
    assert a["type"] == "insert_text"
    assert a["params"]["text"] == "Hello world"
    assert a["requires_mode"] == "compose"
    assert "id" in a


def test_insert_text_skips_empty():
    ctx = FakeCtx()
    out = insert_text_at_cursor(ctx, "")
    assert out["status"] == "skip"
    assert PENDING_ACTIONS_KEY not in ctx.state or not ctx.state[PENDING_ACTIONS_KEY]


def test_replace_body_queues():
    ctx = FakeCtx()
    replace_compose_body(ctx, "<p>Hi</p>", as_html=True)
    a = _last_action(ctx)
    assert a["type"] == "replace_body"
    assert a["params"]["as_html"] is True


def test_set_subject_queues():
    ctx = FakeCtx()
    set_subject(ctx, "Re: Q3 budget")
    a = _last_action(ctx)
    assert a["type"] == "set_subject"
    assert a["params"]["subject"] == "Re: Q3 budget"


def test_add_recipients_parses_csv():
    ctx = FakeCtx()
    out = add_recipients(ctx, "a@x.com, b@x.com , c@x.com", field="cc")
    assert out["status"] == "queued"
    a = _last_action(ctx)
    assert a["type"] == "add_recipients"
    assert a["params"]["field"] == "cc"
    assert a["params"]["addresses"] == ["a@x.com", "b@x.com", "c@x.com"]


def test_add_recipients_rejects_unknown_field():
    ctx = FakeCtx()
    out = add_recipients(ctx, "a@x.com", field="reply-to")
    assert out["status"] == "error"


def test_remove_recipients_lowercases():
    ctx = FakeCtx()
    remove_recipients(ctx, "A@X.com")
    a = _last_action(ctx)
    assert a["params"]["addresses"] == ["a@x.com"]


def test_set_importance_validates():
    ctx = FakeCtx()
    assert set_importance(ctx, "HIGH")["status"] == "queued"
    assert set_importance(ctx, "urgent")["status"] == "error"


def test_attach_file_validates():
    ctx = FakeCtx()
    bad = attach_file_from_url(ctx, "", "")
    assert bad["status"] == "error"
    good = attach_file_from_url(ctx, "https://x/y.pdf", "y.pdf")
    assert good["status"] == "queued"


def test_create_reply_queues_read_mode():
    ctx = FakeCtx()
    create_reply_draft(ctx, body="Got it.", reply_all=True)
    a = _last_action(ctx)
    assert a["type"] == "create_reply"
    assert a["requires_mode"] == "read"
    assert a["params"]["reply_all"] is True


def test_create_forward_parses_recipients():
    ctx = FakeCtx()
    create_forward_draft(ctx, "x@y, z@y", body="FYI")
    a = _last_action(ctx)
    assert a["type"] == "create_forward"
    assert a["params"]["to"] == ["x@y", "z@y"]


def test_apply_categories_and_flag():
    ctx = FakeCtx()
    apply_categories(ctx, "Red, Follow-up")
    assert _last_action(ctx)["params"]["categories"] == ["Red", "Follow-up"]
    set_flag(ctx, "complete")
    assert _last_action(ctx)["params"]["state"] == "complete"


def test_refresh_context_queues():
    ctx = FakeCtx()
    out = refresh_outlook_context(ctx)
    assert out["status"] == "queued"
    assert _last_action(ctx)["type"] == "refresh_context"


def test_get_recent_action_results_reads_state():
    ctx = FakeCtx(
        {
            ACTION_RESULTS_KEY: [
                {"action_id": "1", "type": "insert_text", "status": "ok"},
                {"action_id": "2", "type": "set_subject", "status": "ok"},
                {"action_id": "3", "type": "set_flag", "status": "error", "error": "boom"},
            ]
        }
    )
    out = get_recent_action_results(ctx, limit=2)
    assert out["count"] == 3
    assert len(out["results"]) == 2
    assert out["results"][-1]["status"] == "error"


def test_multiple_actions_accumulate():
    ctx = FakeCtx()
    set_subject(ctx, "X")
    add_recipients(ctx, "a@b")
    insert_text_at_cursor(ctx, "body")
    assert len(ctx.state[PENDING_ACTIONS_KEY]) == 3
    assert [a["type"] for a in ctx.state[PENDING_ACTIONS_KEY]] == [
        "set_subject",
        "add_recipients",
        "insert_text",
    ]
