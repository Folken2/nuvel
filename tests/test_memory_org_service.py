from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from google.adk.memory.base_memory_service import SearchMemoryResponse

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


@dataclass
class StubSearchStore(FakeStore):
    """Fake that returns pre-canned rows from .search regardless of args."""

    canned: list[MemoryRow] = field(default_factory=list)
    last_search: dict = field(default_factory=dict)

    async def search(self, **kwargs) -> list[MemoryRow]:
        self.last_search = kwargs
        return list(self.canned)


@pytest.mark.asyncio
async def test_search_uses_full_user_chain_and_returns_memory_entries():
    store = StubSearchStore(
        canned=[
            MemoryRow(
                id="r1",
                org_id="acme",
                scope_level="team",
                scope_id="platform",
                scope_chain=["team:platform", "division:eu", "org:acme"],
                content="team policy",
                embedding=None,
                score=0.81,
            ),
        ]
    )
    svc = OrgMemoryService(
        store=store,
        resolver=ConfigScopeResolver.from_yaml(FIXTURE),
        embedder=NullEmbedder(),
    )
    resp = await svc.search_memory(app_name="agent", user_id="albert", query="policy")
    assert isinstance(resp, SearchMemoryResponse)
    assert len(resp.memories) == 1
    # MemoryEntry.content is a google.genai Content; extract its text:
    parts = resp.memories[0].content.parts or []
    text = "\n".join(p.text for p in parts if getattr(p, "text", None))
    assert text == "team policy"
    assert store.last_search["org_id"] == "acme"
    assert store.last_search["user_chain_tags"] == [
        "user:albert", "team:platform", "division:eu", "org:acme",
    ]
    assert store.last_search["q_embedding"] is None  # NullEmbedder
    assert store.last_search["query_text"] == "policy"
    assert store.last_search["tier_boost"]["user"] == 1.0
