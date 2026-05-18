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


async def test_update_replaces_topic(service):
    user_id = await service.upsert_user("eve@example.com")
    await service.save(user_id, "eve uses dark mode", topic="user-prefs")
    await service.save(user_id, "eve prefers Slack over email", topic="user-prefs")

    # update() replaces all rows in the topic with a single new row.
    await service.update(user_id, "eve uses dark mode and prefers Slack", topic="user-prefs")

    recall = await service.recall(user_id, topic="user-prefs")
    # Old rows are gone, only the consolidated content remains.
    assert "dark mode and prefers Slack" in recall["content"]
    assert recall["content"].count("eve") == 1


async def test_forget_topic_removes_only_that_topic(service):
    user_id = await service.upsert_user("frank@example.com")
    await service.save(user_id, "core fact", topic="core")
    await service.save(user_id, "topic fact", topic="other")

    result = await service.forget_topic(user_id, "other")
    assert result["status"] == "ok"
    assert result["deleted"] == 1

    # Core still there
    assert "core fact" in (await service.recall(user_id))["content"]
    # Other gone
    assert (await service.recall(user_id, topic="other"))["content"] == ""


async def test_stats_reports_counts(service):
    user_id = await service.upsert_user("gina@example.com")
    await service.save(user_id, "a", topic="core")
    await service.save(user_id, "b", topic="core")
    await service.save(user_id, "c", topic="prefs")

    stats = await service.stats(user_id)
    assert stats["total_rows"] == 3
    assert stats["topics"] == {"core": 2, "prefs": 1}


async def test_search_memory_finds_via_stemming(service):
    user_id = await service.upsert_user("henry@example.com")
    await service.save(user_id, "henry prefers concise emails", topic="user-prefs")
    await service.save(user_id, "henry's car is red", topic="random")
    await service.save(user_id, "weather is nice today", topic="random")

    resp = await service.search_memory(
        app_name="outlook-king-test",  # ignored — service uses its own app_name
        user_id=user_id,
        query="preferences",
    )
    # ADK SearchMemoryResponse has a `memories` list.
    contents = [m.content.parts[0].text for m in resp.memories]
    assert any("concise emails" in c for c in contents)
    # The car/weather rows should not match "preferences" (FTS stemming).
    assert not any("weather" in c for c in contents)
