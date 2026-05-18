"""Tests for NeonMemoryService."""
from __future__ import annotations

import pytest

from outlook_king.state.memory_service import NeonMemoryService


@pytest.fixture
def service(memory_pool):
    return NeonMemoryService(memory_pool, app_name="outlook-king-test")


async def test_upsert_user_creates_new_user(service):
    user_id = await service.upsert_user("alice@example.com", "Alice Smith")
    assert isinstance(user_id, str)
    assert len(user_id) == 36  # UUID


async def test_upsert_user_is_idempotent(service):
    a = await service.upsert_user("bob@example.com", "Bob")
    b = await service.upsert_user("bob@example.com", "Bob")
    assert a == b


async def test_save_then_recall_core(service):
    user_id = await service.upsert_user("carol@example.com")
    save_result = await service.save(user_id, "carol prefers concise replies")
    assert save_result["status"] == "ok"

    recall = await service.recall(user_id)
    assert recall["status"] == "ok"
    assert "concise replies" in recall["content"]


async def test_save_then_recall_topic(service):
    user_id = await service.upsert_user("dave@example.com")
    await service.save(user_id, "dave is a senior PM", topic="user-bio")
    await service.save(user_id, "dave works at Acme", topic="user-bio")

    recall = await service.recall(user_id, topic="user-bio")
    assert "senior PM" in recall["content"]
    assert "Acme" in recall["content"]

    # Core memory is empty
    core = await service.recall(user_id)
    assert core["content"] == ""
