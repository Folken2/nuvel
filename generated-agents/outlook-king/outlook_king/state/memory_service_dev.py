"""In-memory drop-in for NeonMemoryService (DEV_MODE only).

Same method surface and return shapes as ``NeonMemoryService``, backed by
process-local dicts instead of Postgres. State resets on every restart —
intentional, matching the dev semantics of ADK's ``InMemorySessionService``.

Never use in production: no persistence, no cross-process sharing.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Optional
from uuid import NAMESPACE_URL, uuid5

from google.adk.memory.base_memory_service import (
    BaseMemoryService,
    SearchMemoryResponse,
)
from google.adk.sessions import Session


class InMemoryMemoryService(BaseMemoryService):
    """Dict-backed memory service. Construct once per process."""

    def __init__(self, app_name: str) -> None:
        self._app_name = app_name
        self._users: dict[str, str] = {}
        # rows: {id, user_id, topic, content, created_at}
        self._rows: list[dict] = []
        self._next_id = itertools.count(1)

    async def upsert_user(
        self, email: str, display_name: Optional[str] = None
    ) -> str:
        del display_name
        user_id = self._users.get(email)
        if user_id is None:
            # Deterministic so the same email maps to the same id across
            # restarts even though the memories themselves do not survive.
            user_id = str(uuid5(NAMESPACE_URL, f"nuvel-dev-user:{email}"))
            self._users[email] = user_id
        return user_id

    async def save(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        row_id = next(self._next_id)
        self._rows.append(
            {
                "id": row_id,
                "user_id": user_id,
                "topic": topic,
                "content": content,
                "created_at": datetime.now(timezone.utc),
            }
        )
        return {"status": "ok", "id": row_id, "topic": topic}

    async def recall(
        self, user_id: str, topic: Optional[str] = None
    ) -> dict:
        topic_filter = topic or "core"
        contents = [
            r["content"]
            for r in self._rows
            if r["user_id"] == user_id and r["topic"] == topic_filter
        ]
        return {
            "status": "ok",
            "topic": topic_filter,
            "content": "\n\n".join(contents),
        }

    async def update(
        self, user_id: str, content: str, topic: str = "core"
    ) -> dict:
        self._rows = [
            r
            for r in self._rows
            if not (r["user_id"] == user_id and r["topic"] == topic)
        ]
        result = await self.save(user_id, content, topic)
        return result

    async def forget_topic(self, user_id: str, topic: str) -> dict:
        before = len(self._rows)
        self._rows = [
            r
            for r in self._rows
            if not (r["user_id"] == user_id and r["topic"] == topic)
        ]
        return {"status": "ok", "topic": topic, "deleted": before - len(self._rows)}

    async def stats(self, user_id: str) -> dict:
        topics: dict[str, int] = {}
        for r in self._rows:
            if r["user_id"] == user_id:
                topics[r["topic"]] = topics.get(r["topic"], 0) + 1
        return {
            "status": "ok",
            "total_rows": sum(topics.values()),
            "topics": topics,
        }

    # BaseMemoryService interface — same curated-only model as Neon.
    async def add_session_to_memory(self, session: Session) -> None:
        del session
        return None

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        """Naive keyword match (every query word present), newest first, top 10."""
        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai.types import Content, Part

        del app_name  # per-app service, mirroring NeonMemoryService
        words = [w for w in query.lower().split() if w]
        matches = [
            r
            for r in self._rows
            if r["user_id"] == user_id
            and all(w in r["content"].lower() for w in words)
        ]
        matches.sort(key=lambda r: r["created_at"], reverse=True)

        memories = [
            MemoryEntry(
                content=Content(role="user", parts=[Part(text=r["content"])]),
                author=r["topic"],
                timestamp=r["created_at"].isoformat(),
            )
            for r in matches[:10]
        ]
        return SearchMemoryResponse(memories=memories)
