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
