"""ADK BaseMemoryService implementation backed by a MemoryStore + ScopeResolver."""

from __future__ import annotations

import logging
from typing import Any, Iterable, Sequence

# SearchMemoryResponse lives in base_memory_service in ADK 2.x, not a separate module
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.adk.events import Event

from nuvel.memory.embedder import Embedder, NullEmbedder
from nuvel.memory.resolver import ScopeResolver
from nuvel.memory.scope import Scope, ScopeChain
from nuvel.memory.store import MemoryRow, MemoryStore, ScopeAuthorizationError

log = logging.getLogger(__name__)

DEFAULT_TIER_BOOST: dict[str, float] = {
    "user": 1.0,
    "team": 0.9,
    "division": 0.75,
    "country": 0.7,
    "corporate": 0.65,
    "org": 0.6,
}


class OrgMemoryService(BaseMemoryService):
    def __init__(
        self,
        *,
        store: MemoryStore,
        resolver: ScopeResolver,
        embedder: Embedder | None = None,
        tier_boost: dict[str, float] | None = None,
        top_k: int = 10,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._embedder = embedder or NullEmbedder()
        self._tier_boost = tier_boost or DEFAULT_TIER_BOOST
        self._top_k = top_k

    # ── writes ────────────────────────────────────────────────

    async def add_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        memories: Sequence[dict[str, Any] | MemoryEntry],
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        chain = self._resolver.resolve(user_id)
        target, target_chain = self._target_scope(chain, custom_metadata)
        for m in memories:
            content = _extract_content(m)
            await self._insert(
                content=content,
                target=target,
                target_chain=target_chain,
                app_name=app_name,
                session_id=None,
                custom_metadata=custom_metadata,
            )

    async def add_events_to_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        events: Iterable[Event],
        session_id: str | None = None,
        custom_metadata: dict[str, Any] | None = None,
    ) -> None:
        chain = self._resolver.resolve(user_id)
        target, target_chain = self._target_scope(chain, custom_metadata)
        for ev in events:
            text = _event_text(ev)
            if not text:
                continue
            await self._insert(
                content=text,
                target=target,
                target_chain=target_chain,
                app_name=app_name,
                session_id=session_id,
                custom_metadata=custom_metadata,
            )

    async def add_session_to_memory(self, session: Session) -> None:
        await self.add_events_to_memory(
            app_name=session.app_name,
            user_id=session.user_id,
            events=session.events or [],
            session_id=session.id,
        )

    # ── reads (stub — Task 6) ─────────────────────────────────

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        # Implemented in Task 6; return empty response to satisfy abstract method
        return SearchMemoryResponse(memories=[])

    # ── internals ──────────────────────────────────────────────

    def _target_scope(
        self,
        chain: ScopeChain,
        custom_metadata: dict[str, Any] | None,
    ) -> tuple[Scope, list[str]]:
        override = (custom_metadata or {}).get("scope")
        if override is None:
            target = chain.scopes[0]
        else:
            target = Scope(level=override["level"], id=override["id"])
            if not chain.contains(target):
                raise ScopeAuthorizationError(
                    f"user not authorized to write into {target.tag()!r}"
                )
        target_chain = self._chain_from_target(target, chain)
        return target, target_chain

    @staticmethod
    def _chain_from_target(target: Scope, full: ScopeChain) -> list[str]:
        tags = full.tags()
        idx = tags.index(target.tag())
        return tags[idx:]

    async def _insert(
        self,
        *,
        content: str,
        target: Scope,
        target_chain: list[str],
        app_name: str,
        session_id: str | None,
        custom_metadata: dict[str, Any] | None,
    ) -> None:
        embedding = self._embedder.embed(content)
        row = MemoryRow(
            id=None,
            org_id=self._resolver.org_id,
            scope_level=target.level,
            scope_id=target.id,
            scope_chain=target_chain,
            content=content,
            embedding=embedding,
            source_app=app_name,
            source_session=session_id,
            custom_metadata={k: v for k, v in (custom_metadata or {}).items() if k != "scope"},
        )
        await self._store.insert(row)


def _extract_content(m: dict[str, Any] | MemoryEntry) -> str:
    """Extract a plain-text string from a MemoryEntry or dict."""
    if isinstance(m, MemoryEntry):
        # MemoryEntry.content is google.genai.types.Content; extract text from parts
        parts = getattr(m.content, "parts", None) or []
        return "\n".join(p.text for p in parts if getattr(p, "text", None)).strip()
    return str(m["content"])


def _event_text(ev: Event) -> str:
    parts = getattr(getattr(ev, "content", None), "parts", None) or []
    return "\n".join(p.text for p in parts if getattr(p, "text", None)).strip()
