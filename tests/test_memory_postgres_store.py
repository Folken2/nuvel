from __future__ import annotations

import os
import uuid

import pytest

from nuvel.memory.backends.postgres_store import PostgresStore
from nuvel.memory.store import MemoryRow
from tests.memory.store_contract import make_contract_tests

DSN = os.getenv("NUVEL_MEMORY_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NUVEL_MEMORY_TEST_DSN not set")


_store: PostgresStore | None = None


async def _factory() -> PostgresStore:
    global _store
    if _store is None:
        _store = PostgresStore(DSN)  # type: ignore[arg-type]
        await _store.migrate()
    return _store


class TestPostgresContract(make_contract_tests(_factory)):
    pass


@pytest.mark.asyncio
async def test_tier_boost_lets_team_outrank_org_on_equal_text():
    store = await _factory()
    uid = uuid.uuid4().hex[:6]
    team_id = f"t-{uid}"
    team_tag = f"team:{team_id}"
    chain_tags = [f"user:u-{uid}", team_tag, "org:acme"]
    marker = f"shared-policy-{uid}"

    await store.insert(MemoryRow(
        id=None, org_id="acme", scope_level="team", scope_id=team_id,
        scope_chain=[team_tag, "org:acme"], content=marker,
        embedding=None,
    ))
    await store.insert(MemoryRow(
        id=None, org_id="acme", scope_level="org", scope_id="acme",
        scope_chain=["org:acme"], content=marker,
        embedding=None,
    ))
    rows = await store.search(
        org_id="acme", user_chain_tags=chain_tags, q_embedding=None,
        query_text=marker, k=2,
        tier_boost={"team": 0.9, "org": 0.6},
    )
    matching = [r for r in rows if r.content == marker]
    assert matching, "expected at least one row"
    assert matching[0].scope_level == "team"
