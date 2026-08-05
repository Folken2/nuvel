"""Relational-recall arm: typed-edge retrieval over the knowledge graph.

A port of the *shape* of gbrain's ``relational-intent.ts`` +
``relational-recall.ts``, adapted to Nuvel's memory model. Two pieces:

* :func:`parse_relational_query` — pure, regex-only detection of a query whose
  answer is a *relationship* ("who founded Acme", "founders of Acme", "who works
  at Globex"). Deterministic, no LLM, ReDoS-bounded seed captures.
* :func:`relational_recall` — resolve the seed entity against the graph, walk
  the typed edges to related entities, and gather the memories that mention the
  seed and its neighbours. Returns a ranked ``list[MemoryRow]`` that
  ``hybrid.fuse_and_rank`` injects as a third RRF arm.

The graph itself is abstracted behind :class:`GraphView` so the walk logic is
DB-agnostic and unit-testable with an in-memory fake; the Postgres implementation
lives in ``backends/postgres_store.py``. The arm is fail-open and high-precision:
it no-ops (empty list) whenever the query isn't relational, names no known
entity, or the seed doesn't resolve — never breaking the search hot path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from nuvel.memory.extraction import _ENTITY, normalize_entity_name
from nuvel.memory.store import MemoryRow

# Bounded seed capture (1–80 chars) so patterns are anchored and ReDoS-safe.
_SEED = r"(?P<seed>.{1,80}?)"


@dataclass(frozen=True)
class RelationalQuery:
    """A parsed relational query. ``relationship`` is None for a type-agnostic
    walk (any edge touching the seed); ``direction`` is relative to the seed."""

    kind: str  # who_rel | about
    seed: str
    relationship: str | None
    direction: str  # in | out | both


# (compiled regex, relationship, direction). Ordered specific → general; first
# match wins. "in" = walk edges pointing INTO the seed (founders of a company).
_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(rf"\bwho\s+(?:founded|co-?founded|started)\s+{_SEED}\s*\??$", re.I), "founded", "in"),
    (re.compile(rf"\bfounders?\s+of\s+{_SEED}\s*\??$", re.I), "founded", "in"),
    (re.compile(rf"\bwho\s+(?:invested in|funded|backed)\s+{_SEED}\s*\??$", re.I), "invested_in", "in"),
    (re.compile(rf"\binvestors?\s+(?:in|of)\s+{_SEED}\s*\??$", re.I), "invested_in", "in"),
    (re.compile(rf"\bwho\s+(?:works?|worked)\s+at\s+{_SEED}\s*\??$", re.I), "works_at", "in"),
    (re.compile(rf"\bemployees?\s+of\s+{_SEED}\s*\??$", re.I), "works_at", "in"),
    (re.compile(rf"\bwho\s+advises\s+{_SEED}\s*\??$", re.I), "advises", "in"),
    (re.compile(rf"\bwho\s+attended\s+{_SEED}\s*\??$", re.I), "attended", "in"),
]

# "tell me about X", "what do we know about X" → direct-mention recall (no walk).
_ABOUT = re.compile(rf"\b(?:about|regarding|on)\s+{_SEED}\s*\??$", re.I)

_STRIP = " \t\r\n.,;:!?\"'`"


def _clean_seed(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().strip(_STRIP))


def parse_relational_query(query: str) -> RelationalQuery | None:
    """Detect a relational query, else None. First matching pattern wins."""
    if not query or len(query) > 512:
        return None
    for regex, rel, direction in _PATTERNS:
        m = regex.search(query)
        if not m:
            continue
        seed = _clean_seed(m.group("seed"))
        if seed:
            return RelationalQuery(kind="who_rel", seed=seed, relationship=rel, direction=direction)
    m = _ABOUT.search(query)
    if m:
        seed = _clean_seed(m.group("seed"))
        if seed:
            return RelationalQuery(kind="about", seed=seed, relationship=None, direction="both")
    return None


class GraphView(Protocol):
    """Read-side of the knowledge graph the recall arm walks. Async so the
    Postgres implementation can issue queries; the in-memory test fake is
    trivially async too."""

    async def resolve_entity(self, name: str) -> str | None:
        """Return the canonical (normalized) entity key for ``name``, or None if
        it doesn't resolve to a known entity."""
        ...

    async def memories_mentioning(self, canonical: str) -> list[MemoryRow]:
        """Memories whose extracted entities include ``canonical``."""
        ...

    async def counterparts(
        self, canonical: str, relationship: str | None, direction: str
    ) -> list[str]:
        """Canonical names on the other side of typed edges from ``canonical``.

        ``direction`` 'in' returns subjects of edges pointing INTO the seed
        (``* --rel--> seed``), 'out' returns objects (``seed --rel--> *``),
        'both' returns either. ``relationship`` None matches any edge type.
        """
        ...


def _candidate_seeds(query: str) -> list[str]:
    """Capitalized entity phrases in a non-relational query, for the fallback
    'does the query name a known entity?' resolution path."""
    return [m.group(0) for m in re.finditer(_ENTITY, query)]


async def relational_recall(
    query: str,
    graph: GraphView,
    *,
    limit: int = 20,
) -> list[MemoryRow]:
    """Build the relational arm: resolve a seed, walk typed edges, gather
    memories. Empty (pure no-op) when nothing relational resolves. Never raises
    for a graph miss — callers treat any exception as an empty arm.

    Ordering: memories mentioning the seed first (most direct), then memories
    mentioning each walked counterpart, deduped by id, capped at ``limit``.
    """
    parsed = parse_relational_query(query)

    seed_phrases: list[str]
    relationship: str | None
    direction: str
    if parsed is not None:
        seed_phrases = [parsed.seed]
        relationship = parsed.relationship
        direction = parsed.direction
    else:
        # Non-relational: still fire if the query names a known entity (direct
        # recall), otherwise no-op.
        seed_phrases = _candidate_seeds(query)
        relationship = None
        direction = "both"

    out: list[MemoryRow] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()

    def _push(rows: list[MemoryRow]) -> None:
        for r in rows:
            key = r.id or f"h:{hash(r.content)}"
            bucket = seen_ids if r.id else seen_hashes
            if key in bucket:
                continue
            bucket.add(key)
            out.append(r)

    resolved_any = False
    for phrase in seed_phrases:
        canonical = await graph.resolve_entity(phrase)
        if canonical is None:
            continue
        resolved_any = True
        # Direct: memories mentioning the seed itself.
        _push(await graph.memories_mentioning(canonical))
        # Walked: resolve neighbours via typed edges, gather their memories.
        for neighbour in await graph.counterparts(canonical, relationship, direction):
            if neighbour == canonical:
                continue
            _push(await graph.memories_mentioning(neighbour))
        if len(out) >= limit:
            break

    if not resolved_any:
        return []
    return out[:limit]
