"""Multi-tenant Postgres-backed memory service for outlook-king.

Implements the ADK ``BaseMemoryService`` interface for the search seam
and exposes curated CRUD methods (``save``, ``recall``, ``update``,
``forget_topic``, ``stats``) for the explicit memory tools.

All SQL lives in this module. Tools and routes must never issue raw
queries against the memory tables — that is the multi-tenant isolation
boundary. Every memory query filters on ``user_id`` AND ``app_name``.
"""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.memory.base_memory_service import (
    BaseMemoryService,
    SearchMemoryResponse,
)
from google.adk.sessions import Session
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


class NeonMemoryService(BaseMemoryService):
    """Postgres memory service. Construct once per process."""

    def __init__(self, pool: AsyncConnectionPool, app_name: str) -> None:
        self._pool = pool
        self._app_name = app_name

    async def upsert_user(
        self, email: str, display_name: Optional[str] = None
    ) -> str:
        """Insert the user if new, otherwise bump last_seen_at. Returns user_id."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nuvel_memory.users (email, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (email) DO UPDATE
                      SET last_seen_at = now(),
                          display_name = COALESCE(EXCLUDED.display_name, nuvel_memory.users.display_name)
                    RETURNING user_id::text
                    """,
                    (email, display_name),
                )
                row = await cur.fetchone()
                assert row is not None, "RETURNING clause must yield one row"
                return row[0]

    async def save(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        """Append a memory row. Topic defaults to 'core' (the legacy AGENT_MEMORY.md)."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nuvel_memory.memories (user_id, app_name, topic, content)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (user_id, self._app_name, topic, content),
                )
                row = await cur.fetchone()
                assert row is not None, "RETURNING clause must yield one row"
                return {"status": "ok", "id": row[0], "topic": topic}

    async def recall(
        self, user_id: str, topic: Optional[str] = None
    ) -> dict:
        """Return all rows for a topic concatenated. None / '' → 'core'."""
        topic_filter = topic or "core"
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content
                      FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s AND topic = %s
                     ORDER BY created_at ASC
                    """,
                    (user_id, self._app_name, topic_filter),
                )
                rows = await cur.fetchall()
        if not rows:
            return {"status": "ok", "topic": topic_filter, "content": ""}
        return {
            "status": "ok",
            "topic": topic_filter,
            "content": "\n\n".join(r[0] for r in rows),
        }

    async def update(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        """Overwrite-semantic: delete all rows for the topic, insert one new row.

        Used by the agent when it wants to summarize/reorganize, not append.
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM nuvel_memory.memories
                         WHERE user_id = %s AND app_name = %s AND topic = %s
                        """,
                        (user_id, self._app_name, topic),
                    )
                    await cur.execute(
                        """
                        INSERT INTO nuvel_memory.memories (user_id, app_name, topic, content)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, self._app_name, topic, content),
                    )
                    row = await cur.fetchone()
                    assert row is not None, "RETURNING clause must yield one row"
        return {"status": "ok", "id": row[0], "topic": topic}

    async def forget_topic(self, user_id: str, topic: str) -> dict:
        """Delete every row for (user_id, app_name, topic). Returns rowcount."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s AND topic = %s
                    """,
                    (user_id, self._app_name, topic),
                )
                deleted = cur.rowcount
        return {"status": "ok", "topic": topic, "deleted": deleted}

    async def stats(self, user_id: str) -> dict:
        """Return per-topic row counts and total."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT topic, COUNT(*)
                      FROM nuvel_memory.memories
                     WHERE user_id = %s AND app_name = %s
                     GROUP BY topic
                    """,
                    (user_id, self._app_name),
                )
                rows = await cur.fetchall()
        topics = {topic: int(count) for topic, count in rows}
        return {
            "status": "ok",
            "total_rows": sum(topics.values()),
            "topics": topics,
        }

    # BaseMemoryService interface stubs — implemented in later tasks.
    async def add_session_to_memory(self, session: Session) -> None:
        return None

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """FTS query, ranked by ts_rank, top 10. Always scoped to (user_id, self._app_name).

        Note: the ``app_name`` argument from ADK is ignored — we use the
        service's configured ``self._app_name`` to keep tenant scoping
        single-sourced. ADK passes its own ``app_name`` for compatibility
        with multi-app deployments; this service is per-app.
        """
        # Import inside the method so it's lazy and matches ADK's actual export path.
        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai.types import Content, Part

        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content, topic, created_at,
                           ts_rank(fts, plainto_tsquery('english', %s)) AS rank
                      FROM nuvel_memory.memories
                     WHERE user_id = %s
                       AND app_name = %s
                       AND fts @@ plainto_tsquery('english', %s)
                     ORDER BY rank DESC, created_at DESC
                     LIMIT 10
                    """,
                    (query, user_id, self._app_name, query),
                )
                rows = await cur.fetchall()

        memories = [
            MemoryEntry(
                content=Content(role="user", parts=[Part(text=content)]),
                author=topic,
                timestamp=created_at.isoformat(),
            )
            for (content, topic, created_at, _rank) in rows
        ]
        return SearchMemoryResponse(memories=memories)
