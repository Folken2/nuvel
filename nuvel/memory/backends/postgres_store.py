"""Postgres + pgvector implementation of MemoryStore."""

from __future__ import annotations

from pathlib import Path

from nuvel.memory.backends._pool import get_pool
from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow

MIGRATION = Path(__file__).parent / "migrations" / "0001_init.sql"


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def migrate(self) -> None:
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))

    async def insert(self, row: MemoryRow) -> str:
        raise NotImplementedError

    async def search(self, **_: object) -> list[MemoryRow]:
        raise NotImplementedError

    async def move(self, memory_id: str, new_scope: Scope, new_chain: list[str]) -> None:
        raise NotImplementedError

    async def delete(self, memory_id: str) -> None:
        raise NotImplementedError

    async def list_by_scope(self, scope: Scope, limit: int = 100) -> list[MemoryRow]:
        raise NotImplementedError
