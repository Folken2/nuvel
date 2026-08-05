"""Postgres + pgvector implementation of MemoryStore."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from nuvel.memory import hybrid
from nuvel.memory.backends._pool import get_pool
from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow

MIGRATION = Path(__file__).parent / "migrations" / "0001_init.sql"

# Candidate-pool depth per arm before fusion. Wider than k so RRF has ranks to
# fuse and the boost cascade has a tail to re-rank; capped to bound the scan.
_ARM_POOL_MULTIPLIER = 5
_ARM_POOL_MIN = 30


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
        """Hybrid RRF search: concurrent keyword (FTS) + vector (cosine) arms,
        fused via reciprocal rank fusion, then a bounded floor-gated boost
        cascade (tier → recency → access → title), autocut and dedup.

        The two arms scan the same scope-isolated candidate pool concurrently;
        the ranking math lives in :mod:`nuvel.memory.hybrid` so it is testable
        without a database. Falls back to keyword-only when no query embedding
        is available (NullEmbedder / embed failure).
        """
        pool_size = max(_ARM_POOL_MIN, k * _ARM_POOL_MULTIPLIER)
        vector_arm, keyword_arm = await self._run_arms(
            org_id=org_id,
            user_chain_tags=user_chain_tags,
            q_embedding=q_embedding,
            query_text=query_text,
            pool_size=pool_size,
        )
        return hybrid.fuse_and_rank(
            vector_arm=vector_arm,
            keyword_arm=keyword_arm,
            query=query_text,
            tier_boost=tier_boost,
            k=k,
        )

    async def _run_arms(
        self,
        *,
        org_id: str,
        user_chain_tags: list[str],
        q_embedding: list[float] | None,
        query_text: str,
        pool_size: int,
    ) -> tuple[list[MemoryRow], list[MemoryRow]]:
        """Run the vector + keyword arms concurrently, each scope-isolated.

        Both arms return the same columns; ``score`` carries the cosine
        similarity (``1 - cosine_distance``) so the fusion stage can blend it,
        and is 0.0 for rows without an embedding.
        """
        pool = await get_pool(self._dsn)

        async def vector() -> list[MemoryRow]:
            if q_embedding is None:
                return []
            async with pool.acquire() as conn:
                records = await conn.fetch(
                    f"""
                    select {_SELECT_COLS},
                           (1 - (embedding <=> $1)) as score
                    from org_memories
                    where org_id = $2
                      and (scope_level || ':' || scope_id) = any($3)
                      and embedding is not null
                    order by embedding <=> $1
                    limit $4
                    """,
                    q_embedding, org_id, user_chain_tags, pool_size,
                )
            return [_row_from_record(r) for r in records]

        async def keyword() -> list[MemoryRow]:
            # FTS arm (ts_rank) with a trigram-similarity tiebreak so short or
            # fuzzy queries still rank. The score column carries cosine (or 0.0
            # when there's no query embedding) for the downstream blend, NOT the
            # keyword rank — RRF fuses the rank ordering itself. The cosine
            # expression is only spliced in when q_embedding is present so
            # asyncpg never sees an untyped NULL vector parameter.
            if q_embedding is not None:
                score_expr = (
                    "case when embedding is not null "
                    "then (1 - (embedding <=> $5)) else 0.0 end as score"
                )
                fts_args = (org_id, user_chain_tags, query_text, pool_size, q_embedding)
            else:
                score_expr = "0.0 as score"
                fts_args = (org_id, user_chain_tags, query_text, pool_size)
            async with pool.acquire() as conn:
                records = await conn.fetch(
                    f"""
                    select {_SELECT_COLS},
                           {score_expr}
                    from org_memories
                    where org_id = $1
                      and (scope_level || ':' || scope_id) = any($2)
                      and (
                        to_tsvector('english', content)
                          @@ websearch_to_tsquery('english', $3)
                        or similarity(content, $3) > 0.1
                      )
                    order by
                      ts_rank(to_tsvector('english', content),
                              websearch_to_tsquery('english', $3)) desc,
                      similarity(content, $3) desc
                    limit $4
                    """,
                    *fts_args,
                )
            return [_row_from_record(r) for r in records]

        return await asyncio.gather(vector(), keyword())

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


_SELECT_COLS = (
    "id::text, org_id, scope_level, scope_id, scope_chain, "
    "content, embedding, source_app, source_session, "
    "custom_metadata, created_at"
)


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
