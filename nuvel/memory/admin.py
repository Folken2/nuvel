"""Non-ADK operations on memory rows: move, delete, list."""

from __future__ import annotations

from typing import Callable

from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow, MemoryStore


class OrgMemoryAdmin:
    def __init__(
        self,
        *,
        store: MemoryStore,
        chain_for_scope: Callable[[Scope], list[str]],
    ) -> None:
        self._store = store
        self._chain_for_scope = chain_for_scope

    async def move(self, memory_id: str, new_scope: Scope) -> None:
        new_chain = self._chain_for_scope(new_scope)
        await self._store.move(memory_id, new_scope, new_chain)

    async def delete(self, memory_id: str) -> None:
        await self._store.delete(memory_id)

    async def list_by_scope(self, scope: Scope, limit: int = 100) -> list[MemoryRow]:
        return await self._store.list_by_scope(scope, limit)
