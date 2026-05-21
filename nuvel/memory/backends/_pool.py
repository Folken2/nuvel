"""Single shared asyncpg pool, lazily created per DSN."""

from __future__ import annotations

import asyncio

import asyncpg
from pgvector.asyncpg import register_vector  # type: ignore

_pools: dict[str, asyncpg.Pool] = {}
_lock = asyncio.Lock()


async def get_pool(dsn: str, *, min_size: int = 1, max_size: int = 8) -> asyncpg.Pool:
    async with _lock:
        if dsn not in _pools:
            _pools[dsn] = await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                init=_init_conn,
            )
        return _pools[dsn]


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
