"""ADK BaseMemoryService implementation backed by a MemoryStore + ScopeResolver."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Protocol, Sequence

# SearchMemoryResponse lives in base_memory_service in ADK 2.x, not a separate module
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.adk.sessions import Session
from google.adk.events import Event

from nuvel.memory.embedder import Embedder, NullEmbedder
from nuvel.memory.extraction import EntityLink, extract_entity_links
from nuvel.memory.resolver import ScopeResolver
from nuvel.memory.scope import Scope, ScopeChain
from nuvel.memory.store import MemoryRow, MemoryStore, ScopeAuthorizationError

log = logging.getLogger(__name__)


class GraphWriter(Protocol):
    """Persist extracted knowledge-graph edges for a memory. Implemented by the
    Postgres store; write is fire-and-forget from the memory write path."""

    async def write_links(self, memory_id: str, links: list[EntityLink]) -> None: ...

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
        graph_writer: GraphWriter | None = None,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._embedder = embedder or NullEmbedder()
        self._tier_boost = tier_boost or DEFAULT_TIER_BOOST
        self._top_k = top_k
        self._graph_writer = graph_writer
        # Background extraction tasks — fire-and-forget so the write path never
        # blocks on entity extraction / graph persistence. Kept referenced so
        # they aren't GC'd mid-flight (asyncio only holds a weak ref).
        self._extraction_tasks: set[asyncio.Task[None]] = set()

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

    # ── reads ─────────────────────────────────────────────────

    async def search_memory(
        self, *, app_name: str, user_id: str, query: str
    ) -> SearchMemoryResponse:
        chain = self._resolver.resolve(user_id)
        q_embedding = await self._embedder.embed(query)
        rows = await self._store.search(
            org_id=self._resolver.org_id,
            user_chain_tags=chain.tags(),
            q_embedding=q_embedding,
            query_text=query,
            k=self._top_k,
            tier_boost=self._tier_boost,
        )
        return SearchMemoryResponse(
            memories=[self._row_to_entry(r) for r in rows]
        )

    def _row_to_entry(self, r: MemoryRow) -> MemoryEntry:
        # MemoryEntry.content is a google.genai Content. Build one from the
        # row's plain-text content (single text part).
        from google.genai.types import Content, Part
        return MemoryEntry(
            content=Content(parts=[Part(text=r.content)]),
        )

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
        embedding = await self._embedder.embed(content)
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
        memory_id = await self._store.insert(row)
        self._schedule_extraction(memory_id, content)

    # ── knowledge-graph extraction (fire-and-forget) ───────────

    def _schedule_extraction(self, memory_id: str, content: str) -> None:
        """Run zero-LLM entity extraction and persist edges in the background so
        the graph self-wires without blocking the write response. No-ops when no
        graph writer is configured."""
        if self._graph_writer is None:
            return
        task = asyncio.create_task(self._extract_and_persist(memory_id, content))
        self._extraction_tasks.add(task)
        task.add_done_callback(self._extraction_tasks.discard)

    async def _extract_and_persist(self, memory_id: str, content: str) -> None:
        try:
            links = extract_entity_links(content)
            if links and self._graph_writer is not None:
                await self._graph_writer.write_links(memory_id, links)
        except Exception:  # never surface into the write path
            log.exception("knowledge-graph extraction failed for memory %s", memory_id)

    async def drain_extraction(self) -> None:
        """Await all in-flight background extraction tasks. For tests and for
        graceful shutdown — the normal write path never awaits these."""
        while self._extraction_tasks:
            await asyncio.gather(*list(self._extraction_tasks), return_exceptions=True)


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
