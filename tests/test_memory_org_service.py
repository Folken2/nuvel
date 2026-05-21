from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from nuvel.memory import (
    ConfigScopeResolver,
    MemoryRow,
    NullEmbedder,
    ScopeAuthorizationError,
)
from nuvel.memory.org_memory_service import OrgMemoryService

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"


@dataclass
class FakeStore:
    rows: list[MemoryRow] = field(default_factory=list)

    async def insert(self, row: MemoryRow) -> str:
        row.id = f"id-{len(self.rows)}"
        self.rows.append(row)
        return row.id

    async def search(self, **_: object) -> list[MemoryRow]:
        return []

    async def move(self, *_: object, **__: object) -> None: ...
    async def delete(self, *_: object) -> None: ...
    async def list_by_scope(self, *_: object, **__: object) -> list[MemoryRow]:
        return []


def _svc() -> tuple[OrgMemoryService, FakeStore]:
    store = FakeStore()
    svc = OrgMemoryService(
        store=store,
        resolver=ConfigScopeResolver.from_yaml(FIXTURE),
        embedder=NullEmbedder(),
    )
    return svc, store


@pytest.mark.asyncio
async def test_default_write_targets_user_leaf():
    svc, store = _svc()
    await svc.add_memory(app_name="agent", user_id="albert", memories=[{"content": "remember X"}])
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row.scope_level == "user"
    assert row.scope_id == "albert"
    assert row.scope_chain == ["user:albert", "team:platform", "division:eu", "org:acme"]


@pytest.mark.asyncio
async def test_metadata_override_writes_to_team_scope_without_user_prefix():
    svc, store = _svc()
    await svc.add_memory(
        app_name="agent",
        user_id="albert",
        memories=[{"content": "team note"}],
        custom_metadata={"scope": {"level": "team", "id": "platform"}},
    )
    row = store.rows[0]
    assert row.scope_level == "team"
    assert row.scope_chain == ["team:platform", "division:eu", "org:acme"]


@pytest.mark.asyncio
async def test_write_to_scope_outside_chain_raises():
    svc, _ = _svc()
    with pytest.raises(ScopeAuthorizationError):
        await svc.add_memory(
            app_name="agent",
            user_id="albert",
            memories=[{"content": "x"}],
            custom_metadata={"scope": {"level": "team", "id": "growth"}},
        )


@pytest.mark.asyncio
async def test_unknown_user_falls_back_to_user_leaf(caplog):
    svc, store = _svc()
    with caplog.at_level("WARNING"):
        await svc.add_memory(app_name="agent", user_id="ghost", memories=[{"content": "x"}])
    row = store.rows[0]
    assert row.scope_chain == ["user:ghost"]
