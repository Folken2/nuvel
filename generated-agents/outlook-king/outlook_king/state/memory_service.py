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

    # BaseMemoryService interface stubs — implemented in later tasks.
    async def add_session_to_memory(self, session: Session) -> None:
        return None

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        raise NotImplementedError
