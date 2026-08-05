"""
Relevance-conditioned memory surfacing.

Replaces whole-file memory injection: instead of pasting every memory into
the prompt each turn, this retrieves only the chunks relevant to the current
query and injects those. Two backends, tried in order:

1. **OrgMemoryService** (or any ADK ``BaseMemoryService``) attached to the
   invocation context — used when a memory DB is configured. Embedding /
   lexical relevance ranking is owned by the service.
2. **Markdown fallback** — when no memory service is present, rank the
   entries of ``AGENT_MEMORY.md`` + topic files by lexical overlap with the
   query and return the top matches.

Gated by ``NUVEL_MEMORY_PRELOAD`` (default on). Set to ``0`` to fall back to
the legacy whole-file injection in ``state/memory.load_all_memory``.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from ..state.memory import load_all_memory

logger = logging.getLogger(__name__)

ENV_PRELOAD = "NUVEL_MEMORY_PRELOAD"
ENV_TOP_K = "NUVEL_MEMORY_PRELOAD_TOP_K"
DEFAULT_TOP_K = 5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def preload_enabled() -> bool:
    return os.environ.get(ENV_PRELOAD, "1").strip().lower() in ("1", "true", "yes", "on")


def _top_k() -> int:
    try:
        return max(1, int(os.environ.get(ENV_TOP_K, "") or DEFAULT_TOP_K))
    except ValueError:
        return DEFAULT_TOP_K


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _split_chunks(blob: str) -> list[str]:
    """Split aggregated markdown memory into individually rankable chunks."""
    chunks: list[str] = []
    for section in blob.split("\n\n---\n\n"):
        for para in re.split(r"\n{2,}", section.strip()):
            para = para.strip()
            if para:
                chunks.append(para)
    return chunks


def rank_markdown_chunks(query: str, blob: str, *, top_k: int) -> list[str]:
    """Return the ``top_k`` memory chunks most lexically relevant to ``query``.

    Pure function (no I/O) so it is directly unit-testable. Falls back to the
    first ``top_k`` chunks when the query carries no usable tokens.
    """
    chunks = _split_chunks(blob)
    if not chunks:
        return []
    q = _tokens(query)
    if not q:
        return chunks[:top_k]
    scored: list[tuple[float, int, str]] = []
    for idx, chunk in enumerate(chunks):
        overlap = len(q & _tokens(chunk))
        if overlap:
            scored.append((overlap, -idx, chunk))
    scored.sort(reverse=True)
    return [c for _score, _idx, c in scored[:top_k]]


def _extract_query(ctx: Any) -> str:
    """Best-effort extraction of the current user query from an ADK context."""
    content = getattr(ctx, "user_content", None)
    if content is not None:
        parts = getattr(content, "parts", None) or []
        text = " ".join(p.text for p in parts if getattr(p, "text", None))
        if text.strip():
            return text.strip()
    ictx = getattr(ctx, "_invocation_context", None)
    session = getattr(ictx, "session", None)
    for event in reversed(list(getattr(session, "events", []) or [])):
        if getattr(event, "author", None) != "user":
            continue
        econtent = getattr(event, "content", None)
        parts = getattr(econtent, "parts", None) or []
        text = " ".join(p.text for p in parts if getattr(p, "text", None))
        if text.strip():
            return text.strip()
    return ""


async def _search_service(ctx: Any, query: str, top_k: int) -> list[str]:
    ictx = getattr(ctx, "_invocation_context", None)
    service = getattr(ictx, "memory_service", None)
    if service is None or not hasattr(service, "search_memory"):
        return []
    try:
        response = await service.search_memory(
            app_name=getattr(ictx, "app_name", "") or "",
            user_id=getattr(ictx, "user_id", "") or "",
            query=query,
        )
    except Exception:
        logger.warning("preload: memory_service.search_memory failed", exc_info=True)
        return []
    out: list[str] = []
    for entry in getattr(response, "memories", None) or []:
        parts = getattr(getattr(entry, "content", None), "parts", None) or []
        text = " ".join(p.text for p in parts if getattr(p, "text", None)).strip()
        if text:
            out.append(text)
    return out[:top_k]


async def retrieve_memory_block(ctx: Any) -> str:
    """Build the memory block to inject this turn — relevance-conditioned.

    Returns "" when preload is disabled or nothing is relevant. When a memory
    service is attached it is preferred; otherwise the markdown store is
    ranked lexically against the current query.
    """
    if not preload_enabled():
        try:
            return load_all_memory()
        except Exception:
            return ""

    query = _extract_query(ctx)
    top_k = _top_k()

    hits = await _search_service(ctx, query, top_k)
    if not hits:
        try:
            blob = load_all_memory()
        except Exception:
            blob = ""
        hits = rank_markdown_chunks(query, blob, top_k=top_k)

    if not hits:
        return ""
    return "\n\n".join(hits)


__all__ = ["retrieve_memory_block", "rank_markdown_chunks", "preload_enabled", "ENV_PRELOAD"]
