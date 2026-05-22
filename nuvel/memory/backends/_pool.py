"""Single shared asyncpg pool, lazily created per DSN."""

from __future__ import annotations

import asyncio

import asyncpg
from pgvector.asyncpg import register_vector  # type: ignore

_pools: dict[str, asyncpg.Pool] = {}
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Return a lock bound to the running event loop, recreating if needed."""
    global _lock
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if _lock is None or (_lock._loop is not None and _lock._loop.is_closed()):  # type: ignore[attr-defined]
        _lock = asyncio.Lock()
    return _lock


async def get_pool(dsn: str, *, min_size: int = 1, max_size: int = 8) -> asyncpg.Pool:
    lock = _get_lock()
    async with lock:
        existing = _pools.get(dsn)
        if existing is not None:
            # Check if the pool's loop is still alive; if not, discard it.
            try:
                pool_loop = existing.get_loop()  # type: ignore[attr-defined]
            except AttributeError:
                # asyncpg Pool doesn't expose get_loop publicly; introspect _loop
                pool_loop = getattr(existing, "_loop", None)
            if pool_loop is None or pool_loop.is_closed():
                _pools.pop(dsn, None)
                existing = None

        if existing is None:
            _pools[dsn] = await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                init=_init_conn,
            )
        return _pools[dsn]


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
