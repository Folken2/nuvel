from dataclasses import dataclass, field

import pytest

from nuvel.memory import Scope
from nuvel.memory.admin import OrgMemoryAdmin
from nuvel.memory.store import MemoryRow


@dataclass
class RecordingStore:
    calls: list[tuple] = field(default_factory=list)

    async def insert(self, row: MemoryRow) -> str:
        return "x"

    async def search(self, **_): return []

    async def move(self, memory_id, new_scope, new_chain):
        self.calls.append(("move", memory_id, new_scope, new_chain))

    async def delete(self, memory_id):
        self.calls.append(("delete", memory_id))

    async def list_by_scope(self, scope, limit=100):
        self.calls.append(("list", scope, limit))
        return []


@pytest.mark.asyncio
async def test_move_recomputes_chain_from_resolver_levels():
    store = RecordingStore()
    admin = OrgMemoryAdmin(
        store=store,
        chain_for_scope=lambda s: [s.tag(), "org:acme"],
    )
    await admin.move("m1", Scope(level="team", id="platform"))
    assert store.calls == [
        ("move", "m1", Scope(level="team", id="platform"), ["team:platform", "org:acme"]),
    ]


@pytest.mark.asyncio
async def test_delete_and_list_pass_through():
    store = RecordingStore()
    admin = OrgMemoryAdmin(store=store, chain_for_scope=lambda s: [s.tag()])
    await admin.delete("m9")
    await admin.list_by_scope(Scope(level="org", id="acme"), limit=50)
    assert store.calls == [
        ("delete", "m9"),
        ("list", Scope(level="org", id="acme"), 50),
    ]
