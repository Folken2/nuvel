"""Unit tests for the writing-style memory tools.

Backed by NeonMemoryService against the Neon test branch (same pool the
memory_service tests use). The style tools resolve user_id from
``tool_context.state['user_id']``; we hand them a tiny stub that exposes
exactly that.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from outlook_king.state.memory_service import NeonMemoryService
from outlook_king.state import memory_singleton
from outlook_king.tools import style_tools


@pytest_asyncio.fixture
async def style_ctx(memory_pool):
    """Wire a NeonMemoryService into the singleton and return a tool-context stub.

    The stub exposes ``state['user_id']`` (the only thing
    ``_resolve_user_id`` reads). Restores the singleton on teardown so we
    don't leak a test pool into other tests.
    """
    service = NeonMemoryService(memory_pool, app_name="outlook-king-test")
    user_id = await service.upsert_user("style-tester@example.com")
    previous = memory_singleton._service
    memory_singleton._service = service
    try:
        yield SimpleNamespace(state={"user_id": user_id}), user_id, service
    finally:
        memory_singleton._service = previous


async def test_recall_empty(style_ctx):
    ctx, _user_id, _service = style_ctx
    out = await style_tools.recall_writing_style(tool_context=ctx)
    assert out["status"] == "empty"


async def test_learn_then_recall(style_ctx):
    ctx, _user_id, _service = style_ctx
    body = "Hi Anna,\n\nThe report's attached. Let me know.\n\nThanks, J"
    out = await style_tools.learn_style_from_sent_email(
        body, recipient="anna@x", subject="Report", tool_context=ctx
    )
    assert out["status"] == "ok"

    recalled = await style_tools.recall_writing_style(tool_context=ctx)
    assert recalled["status"] == "ok"
    assert "anna@x" in recalled["style"]
    assert "Hi Anna" in recalled["style"]


async def test_consolidate_replaces_content(style_ctx):
    ctx, _user_id, _service = style_ctx
    await style_tools.learn_style_from_sent_email(
        "Quick test.", recipient="x@x", tool_context=ctx
    )
    await style_tools.consolidate_writing_style(
        "# Voice\n- Direct.\n- Plain.\n", tool_context=ctx
    )
    recalled = await style_tools.recall_writing_style(tool_context=ctx)
    assert "# Voice" in recalled["style"]
    assert "Sent sample" not in recalled["style"]


async def test_consolidate_rejects_empty(style_ctx):
    ctx, _user_id, _service = style_ctx
    out = await style_tools.consolidate_writing_style("   ", tool_context=ctx)
    assert out["status"] == "error"


async def test_learn_skips_empty_body(style_ctx):
    ctx, _user_id, _service = style_ctx
    out = await style_tools.learn_style_from_sent_email(
        "", recipient="x@x", tool_context=ctx
    )
    assert out["status"] == "skip"


async def test_record_sent_fingerprint_backend_helper(style_ctx):
    """The backend route bypasses the tool wrapper but writes the same data."""
    _ctx, user_id, service = style_ctx
    body = "Hey team, ship it tomorrow.\n\n-J"
    out = await style_tools.record_sent_fingerprint(
        user_id=user_id, body=body, recipient="team@x", subject="Ship"
    )
    assert out["status"] == "ok"

    recall = await service.recall(user_id, style_tools.STYLE_TOPIC)
    assert "team@x" in recall["content"]
    assert "Hey team" in recall["content"]
