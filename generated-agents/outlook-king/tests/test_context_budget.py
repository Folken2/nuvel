"""
Tests for the context budget plugin's eviction pass.

The pass rewrites outgoing request contents only — stored session
objects must never be mutated — replacing stale heavy tool payloads
with re-fetch stubs while keeping recent results and the protected
tail intact.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from outlook_king.plugins.context_budget_plugin import (  # noqa: E402
    DEFAULT_HEAVY_TOOLS,
    evict_stale_payloads,
)

BIG = "z" * 5_000
HUGE = "z" * 10_000


def _user_text(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _tool_response(name: str, payload: dict) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(name=name, response=payload)
            )
        ],
    )


def _kwargs(**overrides):
    base = dict(
        heavy_tools=set(DEFAULT_HEAVY_TOOLS),
        keep_recent=2,
        heavy_min_chars=1200,
        any_tool_min_chars=8000,
        protect_tail=2,
    )
    base.update(overrides)
    return base


def test_evicts_old_heavy_responses_keeps_recent():
    contents = [
        _user_text("read the contract"),
        _tool_response("read_attachment", {"status": "ok", "text": BIG}),  # old → evict
        _tool_response("read_attachment", {"status": "ok", "text": BIG}),  # recent → keep
        _tool_response("read_attachment", {"status": "ok", "text": BIG}),  # recent → keep
        _user_text("now summarize"),
        _user_text("(tail)"),
    ]
    new_contents, saved = evict_stale_payloads(contents, **_kwargs())
    assert saved > 0
    evicted = new_contents[1].parts[0].function_response.response
    assert evicted["status"] == "elided"
    assert "read_attachment" in evicted["note"]
    # the two most recent heavy results are untouched
    assert new_contents[2].parts[0].function_response.response["text"] == BIG
    assert new_contents[3].parts[0].function_response.response["text"] == BIG


def test_small_responses_survive():
    contents = [
        _tool_response("read_attachment", {"status": "ok", "text": "short"}),
        _tool_response("get_selected_message", {"status": "ok", "subject": "hi"}),
        _user_text("a"),
        _user_text("b"),
        _user_text("c"),
    ]
    new_contents, saved = evict_stale_payloads(contents, **_kwargs(keep_recent=0))
    assert saved == 0
    assert new_contents[0].parts[0].function_response.response["text"] == "short"


def test_any_tool_above_large_threshold_is_evicted():
    contents = [
        _tool_response("OUTLOOK_LIST_MESSAGES", {"status": "ok", "blob": HUGE}),
        _user_text("a"),
        _user_text("b"),
        _user_text("c"),
    ]
    new_contents, saved = evict_stale_payloads(contents, **_kwargs(keep_recent=0))
    assert saved > 0
    assert new_contents[0].parts[0].function_response.response["status"] == "elided"


def test_protected_tail_is_never_touched():
    contents = [
        _user_text("question"),
        _tool_response("read_attachment", {"status": "ok", "text": BIG}),
        _user_text("(tail)"),
    ]
    new_contents, saved = evict_stale_payloads(
        contents, **_kwargs(keep_recent=0, protect_tail=2)
    )
    assert saved == 0
    assert new_contents[1].parts[0].function_response.response["text"] == BIG


def test_inline_binary_outside_tail_is_stubbed():
    pdf_part = types.Part(
        inline_data=types.Blob(mime_type="application/pdf", data=b"%PDF-" + b"x" * 4000)
    )
    contents = [
        types.Content(role="user", parts=[pdf_part]),
        _user_text("a"),
        _user_text("b"),
        _user_text("c"),
    ]
    new_contents, saved = evict_stale_payloads(contents, **_kwargs(keep_recent=0))
    assert saved > 0
    replaced = new_contents[0].parts[0]
    assert replaced.inline_data is None
    assert "load_artifacts" in replaced.text


def test_original_objects_are_not_mutated():
    original = _tool_response("read_attachment", {"status": "ok", "text": BIG})
    contents = [original, _user_text("a"), _user_text("b"), _user_text("c")]
    evict_stale_payloads(contents, **_kwargs(keep_recent=0))
    # the session-owned object still has its full payload
    assert original.parts[0].function_response.response["text"] == BIG


def test_user_and_model_text_is_never_evicted():
    contents = [
        _user_text(HUGE),
        types.Content(role="model", parts=[types.Part(text=HUGE)]),
        _user_text("a"),
        _user_text("b"),
        _user_text("c"),
    ]
    new_contents, saved = evict_stale_payloads(contents, **_kwargs(keep_recent=0))
    assert saved == 0
    assert new_contents[0].parts[0].text == HUGE
