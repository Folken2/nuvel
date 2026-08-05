"""Unit tests for the self-wiring typed knowledge-graph layer.

Two independent, zero-LLM pieces are exercised here, both without a database:

* ``nuvel.memory.extraction`` — pattern-only entity/relationship extraction that
  runs on every memory write (the graph self-wires from prose).
* ``nuvel.memory.relational`` — the relational-recall arm: parse a relational
  query, resolve a seed entity, walk typed edges, and return the related
  memories that hybrid RRF fuses as a third (high-precision, low-recall) arm.

The Postgres wiring of both (entity_links / entity_names tables, the SQL graph
view) is DB-dependent and covered by the live contract tests gated on
NUVEL_MEMORY_TEST_DSN; everything in this file is DB-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuvel.memory import extraction, hybrid, relational
from nuvel.memory.store import MemoryRow

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"


# ── entity extraction ─────────────────────────────────────────────────────


def _links_by_rel(content: str) -> dict[str, extraction.EntityLink]:
    return {l.relationship: l for l in extraction.extract_entity_links(content)}


def test_extracts_founded_relation():
    links = extraction.extract_entity_links("Alice founded Acme Corp.")
    founded = [l for l in links if l.relationship == "founded"]
    assert len(founded) == 1
    edge = founded[0]
    assert edge.subject == "Alice"
    assert edge.obj == "Acme Corp"
    assert edge.subject_type == "person"
    assert edge.obj_type == "company"


def test_extracts_works_at_with_title_metadata():
    links = _links_by_rel("Bob is CTO at Globex.")
    assert "works_at" in links
    edge = links["works_at"]
    assert edge.subject == "Bob"
    assert edge.obj == "Globex"
    assert edge.metadata.get("position") == "CTO"


def test_extracts_plain_works_at():
    links = _links_by_rel("Dana works at Initech.")
    assert links["works_at"].subject == "Dana"
    assert links["works_at"].obj == "Initech"


def test_extracts_invested_in():
    links = _links_by_rel("Sequoia invested in Acme Corp.")
    assert links["invested_in"].subject == "Sequoia"
    assert links["invested_in"].obj == "Acme Corp"


def test_extracts_partnered_with():
    links = _links_by_rel("Acme Corp partnered with Globex.")
    assert links["partner_of"].subject == "Acme Corp"
    assert links["partner_of"].obj == "Globex"


def test_extracts_advises():
    links = _links_by_rel("Carol advises Delta Systems.")
    assert links["advises"].subject == "Carol"
    assert links["advises"].obj == "Delta Systems"


def test_extracts_attended():
    links = _links_by_rel("Dan attended Stanford.")
    assert links["attended"].subject == "Dan"
    assert links["attended"].obj == "Stanford"


def test_no_entities_returns_empty():
    assert extraction.extract_entity_links("the meeting went well and everyone agreed") == []


def test_multiple_relations_in_one_text():
    text = "Alice founded Acme Corp. Sequoia invested in Acme Corp."
    rels = {l.relationship for l in extraction.extract_entity_links(text)}
    assert "founded" in rels
    assert "invested_in" in rels


def test_overlapping_patterns_dedup():
    # The same fact stated twice collapses to a single typed edge.
    text = "Alice founded Acme Corp. Alice founded Acme Corp."
    founded = [l for l in extraction.extract_entity_links(text) if l.relationship == "founded"]
    assert len(founded) == 1


def test_confidence_typed_higher_than_mention():
    typed = extraction.extract_entity_links("Alice founded Acme Corp.")
    mention = extraction.extract_entity_links("Zeta Corp released strong numbers.")
    typed_conf = next(l.confidence for l in typed if l.relationship == "founded")
    mention_conf = next(l.confidence for l in mention if l.relationship == "mentioned")
    assert typed_conf > mention_conf


def test_bare_mentions_extracted():
    links = extraction.extract_entity_links("Zeta Corp released strong numbers.")
    mentioned = [l for l in links if l.relationship == "mentioned"]
    assert any(l.subject == "Zeta Corp" for l in mentioned)


def test_typed_endpoints_not_re_emitted_as_bare_mentions():
    links = extraction.extract_entity_links("Alice founded Acme Corp.")
    mentioned = {l.subject for l in links if l.relationship == "mentioned"}
    assert "Alice" not in mentioned
    assert "Acme Corp" not in mentioned


# ── relational-query parsing ───────────────────────────────────────────────


def test_parse_who_founded():
    q = relational.parse_relational_query("who founded Acme Corp?")
    assert q is not None
    assert q.relationship == "founded"
    assert q.seed == "Acme Corp"


def test_parse_founders_of():
    q = relational.parse_relational_query("founders of Acme Corp")
    assert q is not None
    assert q.relationship == "founded"
    assert q.seed == "Acme Corp"


def test_parse_who_works_at():
    q = relational.parse_relational_query("who works at Globex")
    assert q is not None
    assert q.relationship == "works_at"
    assert q.seed == "Globex"


def test_parse_who_invested_in():
    q = relational.parse_relational_query("who invested in Acme Corp")
    assert q is not None
    assert q.relationship == "invested_in"
    assert q.seed == "Acme Corp"


def test_parse_non_relational_returns_none():
    assert relational.parse_relational_query("what is the refund policy") is None


# ── relational recall (async, in-memory graph fake) ────────────────────────


def _row(content: str, *, id: str) -> MemoryRow:
    return MemoryRow(
        id=id,
        org_id="acme",
        scope_level="user",
        scope_id="u1",
        scope_chain=["user:u1", "org:acme"],
        content=content,
        embedding=None,
        created_at=NOW,
    )


@dataclass
class InMemoryGraph:
    """Fake GraphView: entity → memories, plus typed counterpart edges."""

    mentions: dict[str, list[MemoryRow]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (subj, rel, obj), normalized

    def _known(self) -> set[str]:
        names = set(self.mentions)
        for s, _, o in self.edges:
            names.add(s)
            names.add(o)
        return names

    async def resolve_entity(self, name: str) -> str | None:
        norm = extraction.normalize_entity_name(name)
        return norm if norm in self._known() else None

    async def memories_mentioning(self, canonical: str) -> list[MemoryRow]:
        return list(self.mentions.get(canonical, []))

    async def counterparts(self, canonical: str, relationship: str | None, direction: str) -> list[str]:
        out: list[str] = []
        for s, rel, o in self.edges:
            if relationship is not None and rel != relationship:
                continue
            if direction in ("in", "both") and o == canonical:
                out.append(s)
            if direction in ("out", "both") and s == canonical:
                out.append(o)
        return out


async def test_recall_direct_entity_returns_memories():
    acme = extraction.normalize_entity_name("Acme Corp")
    m = _row("Acme Corp shipped a new product", id="m1")
    graph = InMemoryGraph(mentions={acme: [m]})
    out = await relational.relational_recall("tell me about Acme Corp", graph)
    assert [r.id for r in out] == ["m1"]


async def test_recall_walks_founder_edge_returns_company_memories():
    acme = extraction.normalize_entity_name("Acme Corp")
    alice = extraction.normalize_entity_name("Alice")
    company_mem = _row("Acme Corp raised a Series A", id="c1")
    founder_mem = _row("Alice keynoted the summit", id="f1")
    graph = InMemoryGraph(
        mentions={acme: [company_mem], alice: [founder_mem]},
        edges=[(alice, "founded", acme)],
    )
    out = await relational.relational_recall("who founded Acme Corp", graph)
    ids = {r.id for r in out}
    # The company memory AND the resolved founder's memory both surface.
    assert "c1" in ids
    assert "f1" in ids


async def test_recall_no_entity_in_query_noop():
    graph = InMemoryGraph(mentions={"acme corp": [_row("x", id="m1")]})
    out = await relational.relational_recall("what is the refund policy", graph)
    assert out == []


async def test_recall_entity_not_in_graph_noop():
    graph = InMemoryGraph()  # empty
    out = await relational.relational_recall("who founded Acme Corp", graph)
    assert out == []


# ── hybrid fusion with the relational arm ──────────────────────────────────


def test_relational_arm_adds_candidates():
    kw = _row("keyword hit", id="k1")
    kw.score = 0.4
    rel = _row("relational-only hit", id="r1")
    rel.score = 0.0
    out = hybrid.fuse_and_rank(
        vector_arm=[],
        keyword_arm=[kw],
        relational_arm=[rel],
        query="who founded Acme Corp",
        tier_boost={"user": 1.0},
        k=10,
        enable_autocut=False,
    )
    ids = {r.id for r in out}
    assert "k1" in ids
    assert "r1" in ids  # surfaced only because the relational arm carried it


def test_relational_arm_weight_below_keyword():
    # A row present in BOTH keyword and vector outranks a relational-only row.
    strong = _row("strong dual-arm hit", id="s1")
    strong.score = 0.9
    rel = _row("relational-only", id="r1")
    rel.score = 0.0
    out = hybrid.fuse_and_rank(
        vector_arm=[strong],
        keyword_arm=[strong],
        relational_arm=[rel],
        query="q",
        tier_boost={"user": 1.0},
        k=10,
        enable_autocut=False,
    )
    assert out[0].id == "s1"


def test_relational_arm_absent_is_backward_compatible():
    a = _row("answer", id="a")
    a.score = 0.8
    out = hybrid.fuse_and_rank(
        vector_arm=[a],
        keyword_arm=[a],
        query="q",
        tier_boost={"user": 1.0},
        k=10,
    )
    assert [r.id for r in out] == ["a"]


# ── integration: extraction fires on the write path ────────────────────────


@dataclass
class FakeStore:
    rows: list[MemoryRow] = field(default_factory=list)

    async def insert(self, row: MemoryRow) -> str:
        row.id = f"mem-{len(self.rows)}"
        self.rows.append(row)
        return row.id

    async def search(self, **_: object) -> list[MemoryRow]:
        return []

    async def move(self, *_: object, **__: object) -> None: ...
    async def delete(self, *_: object) -> None: ...
    async def list_by_scope(self, *_: object, **__: object) -> list[MemoryRow]:
        return list(self.rows)


@dataclass
class FakeGraphWriter:
    calls: list[tuple[str, list[extraction.EntityLink]]] = field(default_factory=list)

    async def write_links(self, memory_id: str, links: list[extraction.EntityLink]) -> None:
        self.calls.append((memory_id, links))


async def test_extraction_fires_on_add_memory():
    from nuvel.memory import ConfigScopeResolver
    from nuvel.memory.org_memory_service import OrgMemoryService

    store = FakeStore()
    writer = FakeGraphWriter()
    svc = OrgMemoryService(
        store=store,
        resolver=ConfigScopeResolver.from_yaml(FIXTURE),
        graph_writer=writer,
    )

    await svc.add_memory(
        app_name="app",
        user_id="albert",
        memories=[{"content": "Alice founded Acme Corp."}],
    )
    await svc.drain_extraction()

    assert len(writer.calls) == 1
    memory_id, links = writer.calls[0]
    assert memory_id == "mem-0"
    assert any(l.relationship == "founded" and l.subject == "Alice" for l in links)


async def test_extraction_noop_when_no_graph_writer():
    from nuvel.memory import ConfigScopeResolver
    from nuvel.memory.org_memory_service import OrgMemoryService

    store = FakeStore()
    svc = OrgMemoryService(
        store=store, resolver=ConfigScopeResolver.from_yaml(FIXTURE)
    )  # no graph_writer
    await svc.add_memory(
        app_name="app", user_id="albert", memories=[{"content": "Alice founded Acme Corp."}]
    )
    await svc.drain_extraction()  # must not raise
    assert len(store.rows) == 1
