"""Postgres + pgvector implementation of MemoryStore."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from nuvel.memory import hybrid, relational
from nuvel.memory.backends._pool import get_pool
from nuvel.memory.extraction import EntityLink, normalize_entity_name
from nuvel.memory.scope import Scope
from nuvel.memory.store import MemoryRow

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Applied in filename order; each file is idempotent so re-running is safe.
MIGRATIONS = sorted(_MIGRATIONS_DIR.glob("[0-9]*.sql"))

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
            for migration in MIGRATIONS:
                await conn.execute(migration.read_text(encoding="utf-8"))
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

    # ── knowledge graph ───────────────────────────────────────

    async def write_links(self, memory_id: str, links: list[EntityLink]) -> None:
        """Persist extracted typed edges + bare mentions for a memory (the
        :class:`GraphWriter` contract). Called fire-and-forget off the write
        path. A typed binary relation is stored as TWO directed rows (one per
        endpoint, each carrying the counterpart in metadata) so the graph is
        walkable from either side; a bare mention is a single row. Every entity
        endpoint is upserted into ``entity_names`` for seed resolution."""
        if not links:
            return
        link_rows, name_rows = _expand_links(memory_id, links)
        if not link_rows:
            return
        pool = await get_pool(self._dsn)
        # org_id is denormalized onto entity_links so resolution/recall can
        # filter without joining org_memories; read it back from the memory.
        async with pool.acquire() as conn:
            org_id = await conn.fetchval(
                "select org_id from org_memories where id = $1::uuid", memory_id
            )
            if org_id is None:  # memory vanished before extraction landed
                return
            await conn.executemany(
                """
                insert into entity_links
                  (source_memory_id, org_id, target_entity_type,
                   target_entity_name, target_entity_norm, relationship_type,
                   confidence, metadata)
                values ($1::uuid,$2,$3,$4,$5,$6,$7,$8::jsonb)
                """,
                [
                    (memory_id, org_id, t_type, t_name, t_norm, rel, conf, json.dumps(meta))
                    for (t_type, t_name, t_norm, rel, conf, meta) in link_rows
                ],
            )
            await conn.executemany(
                """
                insert into entity_names
                  (org_id, entity_norm, display_name, entity_type, mention_count)
                values ($1,$2,$3,$4,1)
                on conflict (org_id, entity_norm) do update
                  set mention_count = entity_names.mention_count + 1,
                      updated_at = now(),
                      display_name = case
                        when entity_names.entity_type = 'unknown'
                          and excluded.entity_type <> 'unknown'
                        then excluded.display_name else entity_names.display_name end,
                      entity_type = case
                        when entity_names.entity_type = 'unknown'
                        then excluded.entity_type else entity_names.entity_type end
                """,
                [(org_id, norm, name, etype) for (norm, name, etype) in name_rows],
            )

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
        arms_task = self._run_arms(
            org_id=org_id,
            user_chain_tags=user_chain_tags,
            q_embedding=q_embedding,
            query_text=query_text,
            pool_size=pool_size,
        )
        # Third arm: typed-edge relational recall (high-precision, low-recall).
        # Fail-open — a graph miss or error yields an empty arm, never breaking
        # the keyword+vector hot path.
        graph = _PgGraphView(self, org_id=org_id, user_chain_tags=user_chain_tags, limit=pool_size)
        relational_task = self._relational_arm(query_text, graph)

        (vector_arm, keyword_arm), relational_arm = await asyncio.gather(
            arms_task, relational_task
        )
        return hybrid.fuse_and_rank(
            vector_arm=vector_arm,
            keyword_arm=keyword_arm,
            relational_arm=relational_arm,
            query=query_text,
            tier_boost=tier_boost,
            k=k,
        )

    @staticmethod
    async def _relational_arm(
        query_text: str, graph: "_PgGraphView"
    ) -> list[MemoryRow]:
        try:
            return await relational.relational_recall(query_text, graph)
        except Exception:  # fail-open: relational recall never breaks search
            return []

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


# Trigram-similarity floor for fuzzy seed→entity resolution (pg_trgm).
_ENTITY_RESOLVE_SIM = 0.45

_SELECT_COLS = (
    "id::text, org_id, scope_level, scope_id, scope_chain, "
    "content, embedding, source_app, source_session, "
    "custom_metadata, created_at"
)

# (target_type, target_name, target_norm, relationship, confidence, metadata)
_LinkRow = tuple[str, str, str, str, float, dict[str, Any]]
# (entity_norm, display_name, entity_type)
_NameRow = tuple[str, str, str]


def _expand_links(
    memory_id: str, links: list[EntityLink]
) -> tuple[list[_LinkRow], list[_NameRow]]:
    """Flatten EntityLinks into entity_links + entity_names rows.

    A typed binary edge yields two directed rows (subject-side + object-side),
    each stamping the counterpart (normalized + display) and a ``role`` into
    metadata so :meth:`_PgGraphView.counterparts` can walk from either end. A
    bare mention yields one row. Names are deduped for the entity_names upsert.
    """
    link_rows: list[_LinkRow] = []
    names: dict[str, _NameRow] = {}

    def _name(norm: str, display: str, etype: str) -> None:
        # Prefer a typed classification over 'unknown' if we see both.
        prev = names.get(norm)
        if prev is None or (prev[2] == "unknown" and etype != "unknown"):
            names[norm] = (norm, display, etype)

    for link in links:
        s_norm = normalize_entity_name(link.subject)
        _name(s_norm, link.subject, link.subject_type)
        if link.obj is None:
            link_rows.append(
                (link.subject_type, link.subject, s_norm, link.relationship,
                 link.confidence, dict(link.metadata))
            )
            continue
        o_norm = normalize_entity_name(link.obj)
        _name(o_norm, link.obj, link.obj_type or "unknown")
        base = dict(link.metadata)
        # subject-side row (seed is subject → counterpart is the object)
        link_rows.append((
            link.subject_type, link.subject, s_norm, link.relationship, link.confidence,
            {**base, "role": "subject", "counterpart_norm": o_norm, "counterpart_name": link.obj},
        ))
        # object-side row (seed is object → counterpart is the subject)
        link_rows.append((
            link.obj_type or "unknown", link.obj, o_norm, link.relationship, link.confidence,
            {**base, "role": "object", "counterpart_norm": s_norm, "counterpart_name": link.subject},
        ))

    return link_rows, list(names.values())


class _PgGraphView:
    """Postgres-backed :class:`nuvel.memory.relational.GraphView`. Scope-isolated
    to the caller's chain so relational recall never leaks cross-scope memories.
    All queries are read-only and fail-open is handled by the caller."""

    def __init__(
        self, store: "PostgresStore", *, org_id: str, user_chain_tags: list[str], limit: int
    ) -> None:
        self._store = store
        self._org_id = org_id
        self._tags = user_chain_tags
        self._limit = limit

    async def resolve_entity(self, name: str) -> str | None:
        norm = normalize_entity_name(name)
        if not norm:
            return None
        pool = await get_pool(self._store._dsn)
        async with pool.acquire() as conn:
            # Exact normalized hit first; else best trigram match above floor.
            hit = await conn.fetchval(
                """
                select entity_norm from entity_names
                where org_id = $1
                  and (entity_norm = $2 or similarity(entity_norm, $2) > $3)
                order by (entity_norm = $2) desc, similarity(entity_norm, $2) desc,
                         mention_count desc
                limit 1
                """,
                self._org_id, norm, _ENTITY_RESOLVE_SIM,
            )
        return hit

    async def memories_mentioning(self, canonical: str) -> list[MemoryRow]:
        pool = await get_pool(self._store._dsn)
        async with pool.acquire() as conn:
            records = await conn.fetch(
                f"""
                select {_SELECT_COLS}, 0.0 as score
                from org_memories m
                where m.org_id = $1
                  and (m.scope_level || ':' || m.scope_id) = any($2)
                  and m.id in (
                    select el.source_memory_id from entity_links el
                    where el.org_id = $1 and el.target_entity_norm = $3
                  )
                order by m.created_at desc
                limit $4
                """,
                self._org_id, self._tags, canonical, self._limit,
            )
        return [_row_from_record(r) for r in records]

    async def counterparts(
        self, canonical: str, relationship: str | None, direction: str
    ) -> list[str]:
        # Map walk direction to the stored role: seed as object → counterpart is
        # the subject ('in'); seed as subject → counterpart is object ('out').
        roles: list[str] = []
        if direction in ("in", "both"):
            roles.append("object")
        if direction in ("out", "both"):
            roles.append("subject")
        pool = await get_pool(self._store._dsn)
        async with pool.acquire() as conn:
            records = await conn.fetch(
                """
                select distinct metadata->>'counterpart_norm' as cp
                from entity_links
                where org_id = $1
                  and target_entity_norm = $2
                  and metadata->>'counterpart_norm' is not null
                  and metadata->>'role' = any($3)
                  and ($4::text is null or relationship_type = $4)
                """,
                self._org_id, canonical, roles, relationship,
            )
        return [r["cp"] for r in records if r["cp"]]


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
