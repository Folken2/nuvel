"""Postgres + pgvector implementation of MemoryStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import asyncpg

from nuvel.memory.backends._pool import get_pool
from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow

MIGRATION = Path(__file__).parent / "migrations" / "0001_init.sql"


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def migrate(self) -> None:
        # Use a plain connection (no vector codec) so CREATE EXTENSION vector
        # can succeed before the pool tries to register the type codec.
        conn = await asyncpg.connect(self._dsn)
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))
        finally:
            await conn.close()

    async def insert(self, row: MemoryRow) -> str:
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            new_id = await conn.fetchval(
                """
                insert into org_memories
                  (org_id, scope_level, scope_id, scope_chain, content,
                   embedding, source_app, source_session, custom_metadata)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                returning id::text
                """,
                row.org_id, row.scope_level, row.scope_id, row.scope_chain,
                row.content, row.embedding, row.source_app, row.source_session,
                json.dumps(row.custom_metadata),
            )
            row.id = new_id
            return new_id

    async def search(
        self,
        *,
        org_id: str,
        user_chain_tags: list[str],
        q_embedding: list[float] | None,
        query_text: str,
        k: int,
        tier_boost: dict[str, float],
    ) -> list[MemoryRow]:
        boost_case = _render_boost_case(tier_boost)
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            if q_embedding is not None:
                sql = f"""
                select id::text, org_id, scope_level, scope_id, scope_chain,
                       content, embedding, source_app, source_session,
                       custom_metadata, created_at,
                       (1 - (embedding <=> $1)) * ({boost_case}) as score
                from org_memories
                where org_id = $2
                  and (scope_level || ':' || scope_id) = any($3)
                  and embedding is not null
                union all
                select id::text, org_id, scope_level, scope_id, scope_chain,
                       content, embedding, source_app, source_session,
                       custom_metadata, created_at,
                       similarity(content, $4) * 0.5 * ({boost_case}) as score
                from org_memories
                where org_id = $2
                  and (scope_level || ':' || scope_id) = any($3)
                  and embedding is null
                order by score desc nulls last
                limit $5
                """
                records = await conn.fetch(sql, q_embedding, org_id, user_chain_tags, query_text, k)
            else:
                sql = f"""
                select id::text, org_id, scope_level, scope_id, scope_chain,
                       content, embedding, source_app, source_session,
                       custom_metadata, created_at,
                       similarity(content, $1) * ({boost_case}) as score
                from org_memories
                where org_id = $2
                  and (scope_level || ':' || scope_id) = any($3)
                order by score desc nulls last
                limit $4
                """
                records = await conn.fetch(sql, query_text, org_id, user_chain_tags, k)
        return [_row_from_record(r) for r in records]

    async def move(self, memory_id: str, new_scope: Scope, new_chain: list[str]) -> None:
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                update org_memories
                   set scope_level = $1, scope_id = $2, scope_chain = $3, updated_at = now()
                 where id = $4::uuid
                """,
                new_scope.level, new_scope.id, new_chain, memory_id,
            )

    async def delete(self, memory_id: str) -> None:
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            await conn.execute("delete from org_memories where id = $1::uuid", memory_id)

    async def list_by_scope(self, *, org_id: str, scope: Scope, limit: int = 100) -> list[MemoryRow]:
        pool = await get_pool(self._dsn)
        async with pool.acquire() as conn:
            records = await conn.fetch(
                """
                select id::text, org_id, scope_level, scope_id, scope_chain,
                       content, embedding, source_app, source_session,
                       custom_metadata, created_at
                from org_memories
                where org_id = $3 and scope_level = $1 and scope_id = $2
                order by created_at desc
                limit $4
                """,
                scope.level, scope.id, org_id, limit,
            )
        return [_row_from_record(r) for r in records]


def _render_boost_case(tier_boost: dict[str, float]) -> str:
    parts = " ".join(f"when '{lvl}' then {val}" for lvl, val in tier_boost.items())
    return f"case scope_level {parts} else 0.5 end"


def _row_from_record(rec: Any) -> MemoryRow:
    raw_meta = rec["custom_metadata"]
    meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta or "{}")
    embedding = rec["embedding"]
    return MemoryRow(
        id=rec["id"],
        org_id=rec["org_id"],
        scope_level=rec["scope_level"],
        scope_id=rec["scope_id"],
        scope_chain=list(rec["scope_chain"]),
        content=rec["content"],
        embedding=list(embedding) if embedding is not None else None,
        source_app=rec["source_app"] if "source_app" in rec else None,
        source_session=rec["source_session"] if "source_session" in rec else None,
        custom_metadata=meta,
        created_at=rec["created_at"] if "created_at" in rec else None,
        score=rec["score"] if "score" in rec else None,
    )
