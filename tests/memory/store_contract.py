"""Behavioral contract any MemoryStore impl must satisfy.

Consumed by backend-specific test modules via `make_contract_tests(store_factory)`.
"""

from __future__ import annotations

import uuid
from typing import Awaitable, Callable

import pytest

from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow, MemoryStore

StoreFactory = Callable[[], Awaitable[MemoryStore]]


def _row(content: str, scope_level: str, scope_id: str, chain: list[str], embedding=None) -> MemoryRow:
    return MemoryRow(
        id=None,
        org_id="acme",
        scope_level=scope_level,
        scope_id=scope_id,
        scope_chain=chain,
        content=content,
        embedding=embedding,
    )


def make_contract_tests(store_factory: StoreFactory) -> type:
    class _Contract:
        @pytest.mark.asyncio
        async def test_insert_returns_id_and_list_finds_row(self):
            store = await store_factory()
            uid = uuid.uuid4().hex[:6]
            row = _row("hello", "user", f"u-{uid}", [f"user:u-{uid}", "org:acme"])
            new_id = await store.insert(row)
            assert new_id
            found = await store.list_by_scope(org_id="acme", scope=Scope(level=row.scope_level, id=row.scope_id))
            assert any(r.content == "hello" for r in found)

        @pytest.mark.asyncio
        async def test_search_returns_rows_in_user_chain_only(self):
            store = await store_factory()
            uid = uuid.uuid4().hex[:6]
            tag = f"user:{uid}"
            await store.insert(_row(f"mine-{uid}", "user", uid, [tag, "org:acme"]))
            await store.insert(_row(f"not-mine-{uid}", "user", f"ghost-{uid}", [f"user:ghost-{uid}", "org:acme"]))
            rows = await store.search(
                org_id="acme",
                user_chain_tags=[tag, "org:acme"],
                q_embedding=None,
                query_text=f"mine-{uid}",
                k=10,
                tier_boost={"user": 1.0, "org": 0.6},
            )
            contents = [r.content for r in rows]
            assert f"mine-{uid}" in contents
            assert f"not-mine-{uid}" not in contents

        @pytest.mark.asyncio
        async def test_move_updates_scope_and_chain(self):
            store = await store_factory()
            uid = uuid.uuid4().hex[:6]
            team_id = f"team-{uid}"
            mid = await store.insert(_row("movable", "user", uid, [f"user:{uid}", "org:acme"]))
            await store.move(mid, Scope(level="team", id=team_id),
                             [f"team:{team_id}", "org:acme"])
            in_team = await store.list_by_scope(org_id="acme", scope=Scope(level="team", id=team_id))
            assert any(r.id == mid for r in in_team)

        @pytest.mark.asyncio
        async def test_delete_removes_row(self):
            store = await store_factory()
            uid = uuid.uuid4().hex[:6]
            mid = await store.insert(_row("del", "user", uid, [f"user:{uid}", "org:acme"]))
            await store.delete(mid)
            in_scope = await store.list_by_scope(org_id="acme", scope=Scope(level="user", id=uid))
            assert all(r.id != mid for r in in_scope)

    return _Contract
