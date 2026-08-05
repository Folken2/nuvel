"""Relational-recall arm tests — the third (graph) RRF arm.

Two layers, mirroring the repo's split for the memory stack:

* **In-memory** (always run): an org-scoped :class:`GraphView` fake drives the
  pure ``relational.relational_recall`` walk and the ``hybrid.fuse_and_rank``
  fusion, so the arm's contract — resolve seed → walk typed edges → gather
  memories, no-op when nothing resolves, fused UNDER keyword/vector — is
  verified without a database.
* **Postgres-backed** (gated on ``NUVEL_MEMORY_TEST_DSN``): the same behaviours
  through the real ``entity_names`` lookup, ``entity_links`` walk and
  ``PostgresStore.search`` so the SQL scope isolation (org_id + scope chain) is
  exercised end to end.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from nuvel.memory import hybrid, relational
from nuvel.memory.extraction import EntityLink, normalize_entity_name
from nuvel.memory.store import MemoryRow

NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _row(content: str, *, id: str, org_id: str = "acme") -> MemoryRow:
    return MemoryRow(
        id=id,
        org_id=org_id,
        scope_level="user",
        scope_id="u1",
        scope_chain=["user:u1", f"org:{org_id}"],
        content=content,
        embedding=None,
        created_at=NOW,
    )


@dataclass
class OrgScopedGraph:
    """In-memory :class:`relational.GraphView` bound to a single org, mirroring
    how ``_PgGraphView`` is constructed per-org so recall never crosses tenants.

    ``mentions`` maps normalized entity → memories (only this org's rows); a
    resolvable entity is any key present in ``mentions`` or on an edge endpoint.
    ``edges`` are ``(subject_norm, relationship, object_norm)`` triples.
    """

    org_id: str = "acme"
    mentions: dict[str, list[MemoryRow]] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)

    def _known(self) -> set[str]:
        names = set(self.mentions)
        for s, _, o in self.edges:
            names.add(s)
            names.add(o)
        return names

    async def resolve_entity(self, name: str) -> str | None:
        norm = normalize_entity_name(name)
        return norm if norm in self._known() else None

    async def memories_mentioning(self, canonical: str) -> list[MemoryRow]:
        # Only ever this org's rows — the fake is constructed per org.
        return list(self.mentions.get(canonical, []))

    async def counterparts(
        self, canonical: str, relationship: str | None, direction: str
    ) -> list[str]:
        out: list[str] = []
        for s, rel, o in self.edges:
            if relationship is not None and rel != relationship:
                continue
            if direction in ("in", "both") and o == canonical:
                out.append(s)
            if direction in ("out", "both") and s == canonical:
                out.append(o)
        return out


def _norm(name: str) -> str:
    return normalize_entity_name(name)


# ── entity lookup + direct recall ──────────────────────────────────────────


async def test_entity_lookup_returns_linked_memory_ids():
    m1 = _row("Acme Corp shipped a product", id="m1")
    m2 = _row("Acme Corp opened an office", id="m2")
    graph = OrgScopedGraph(mentions={_norm("Acme Corp"): [m1, m2]})
    out = await relational.relational_recall("Acme Corp", graph)
    assert {r.id for r in out} == {"m1", "m2"}


async def test_direct_relationship_about_query():
    m1 = _row("Acme Corp raised a Series A", id="m1")
    graph = OrgScopedGraph(mentions={_norm("Acme"): [m1]})
    out = await relational.relational_recall("Tell me about Acme", graph)
    assert [r.id for r in out] == ["m1"]


# ── walked relationship ────────────────────────────────────────────────────


async def test_walked_relationship_resolves_founder_memories():
    acme, alice = _norm("Acme"), _norm("Alice")
    company_mem = _row("Acme announced record revenue", id="c1")
    founder_mem = _row("Alice gave the keynote", id="f1")
    graph = OrgScopedGraph(
        mentions={acme: [company_mem], alice: [founder_mem]},
        edges=[(alice, "founded", acme)],
    )
    out = await relational.relational_recall("Who founded Acme", graph)
    ids = {r.id for r in out}
    # Both the company row (direct) and the resolved founder's row (one hop).
    assert "c1" in ids
    assert "f1" in ids


# ── no-op paths ────────────────────────────────────────────────────────────


async def test_no_entity_in_query_is_noop():
    graph = OrgScopedGraph(mentions={_norm("Acme"): [_row("x", id="m1")]})
    out = await relational.relational_recall("what is the refund policy", graph)
    assert out == []


async def test_entity_not_in_graph_is_noop():
    graph = OrgScopedGraph()  # nothing known
    out = await relational.relational_recall("Who founded Acme", graph)
    assert out == []


async def test_entity_with_no_links_returns_empty():
    # Entity resolves (it's an edge endpoint) but has no memories mentioning it
    # and no walkable counterparts on the queried relationship → empty arm.
    graph = OrgScopedGraph(edges=[(_norm("Globex"), "acquired", _norm("Initech"))])
    out = await relational.relational_recall("Who founded Acme", graph)
    assert out == []


# ── scope isolation ────────────────────────────────────────────────────────


async def test_scope_isolation_only_own_org_memories():
    # Two orgs each mention "Acme"; a graph bound to org=acme must only ever
    # surface acme's rows (the other org's data is not in this GraphView).
    acme_mem = _row("Acme deal closed", id="a1", org_id="acme")
    graph = OrgScopedGraph(org_id="acme", mentions={_norm("Acme"): [acme_mem]})
    out = await relational.relational_recall("Tell me about Acme", graph)
    assert [r.id for r in out] == ["a1"]
    assert all(r.org_id == "acme" for r in out)


# ── fusion integration ─────────────────────────────────────────────────────


def test_graph_arm_adds_results_over_keyword_only():
    kw = _row("keyword-only hit", id="k1")
    kw.score = 0.5
    graph_only = _row("graph-only hit", id="g1")
    graph_only.score = 0.0

    without_graph = hybrid.fuse_and_rank(
        vector_arm=[],
        keyword_arm=[kw],
        query="Who founded Acme",
        tier_boost={"user": 1.0},
        k=10,
        enable_autocut=False,
    )
    with_graph = hybrid.fuse_and_rank(
        vector_arm=[],
        keyword_arm=[kw],
        relational_arm=[graph_only],
        query="Who founded Acme",
        tier_boost={"user": 1.0},
        k=10,
        enable_autocut=False,
    )
    assert {r.id for r in without_graph} == {"k1"}
    assert {r.id for r in with_graph} == {"k1", "g1"}
    assert len(with_graph) > len(without_graph)


def test_graph_arm_is_lower_weight_than_keyword_vector():
    # A row carried only by the relational arm must not outrank a row present in
    # both keyword and vector at the same rank.
    dual = _row("dual-arm hit", id="d1")
    dual.score = 0.8
    graph_only = _row("graph-only hit", id="g1")
    graph_only.score = 0.0
    out = hybrid.fuse_and_rank(
        vector_arm=[dual],
        keyword_arm=[dual],
        relational_arm=[graph_only],
        query="q",
        tier_boost={"user": 1.0},
        k=10,
        enable_autocut=False,
    )
    assert out[0].id == "d1"
    assert hybrid.RELATIONAL_WEIGHT < 1.0


def test_graph_only_rank1_stays_below_dual_arm_rank1():
    # Same rank (0) in every arm: dual-arm (weight 1+1) must beat single graph
    # arm (weight RELATIONAL_WEIGHT < 1) purely on the RRF contribution.
    dual = _row("dual", id="d1")
    dual.score = 0.0
    solo = _row("solo graph", id="g1")
    solo.score = 0.0
    fused = hybrid.reciprocal_rank_fusion(
        [[dual], [dual], [solo]],
        weights=[1.0, 1.0, hybrid.RELATIONAL_WEIGHT],
    )
    assert fused[hybrid._key(dual)] > fused[hybrid._key(solo)]


# ─────────────────────────────────────────────────────────────────────────────
# Postgres-backed: real entity_names lookup, entity_links walk, scope isolation.
# ─────────────────────────────────────────────────────────────────────────────

DSN = os.getenv("NUVEL_MEMORY_TEST_DSN")
db = pytest.mark.skipif(not DSN, reason="NUVEL_MEMORY_TEST_DSN not set")

_store = None


async def _factory():
    from nuvel.memory.backends.postgres_store import PostgresStore

    global _store
    if _store is None:
        _store = PostgresStore(DSN)  # type: ignore[arg-type]
        await _store.migrate()
    return _store


def _mem(content: str, *, org_id: str, scope_id: str) -> MemoryRow:
    return MemoryRow(
        id=None,
        org_id=org_id,
        scope_level="user",
        scope_id=scope_id,
        scope_chain=[f"user:{scope_id}", f"org:{org_id}"],
        content=content,
        embedding=None,
    )


@db
@pytest.mark.asyncio
async def test_db_entity_lookup_and_direct_recall():
    from nuvel.memory.backends.postgres_store import _PgGraphView

    store = await _factory()
    uid = uuid.uuid4().hex[:6]
    org, sid = f"org-{uid}", f"u-{uid}"
    name = f"Acme-{uid}"
    mem_id = await store.insert(_mem(f"{name} shipped a product.", org_id=org, scope_id=sid))
    await store.write_links(
        mem_id,
        [EntityLink(name, "company", "mentioned", None, None, 0.4, {})],
    )

    graph = _PgGraphView(
        store, org_id=org, user_chain_tags=[f"user:{sid}", f"org:{org}"], limit=20
    )
    canonical = await graph.resolve_entity(name)
    assert canonical == normalize_entity_name(name)
    rows = await graph.memories_mentioning(canonical)
    assert [r.id for r in rows] == [mem_id]


@db
@pytest.mark.asyncio
async def test_db_walked_founder_edge():
    store = await _factory()
    uid = uuid.uuid4().hex[:6]
    org, sid = f"org-{uid}", f"u-{uid}"
    company, founder = f"Acme-{uid}", f"Alice-{uid}"

    company_mem = await store.insert(_mem(f"{company} raised a round.", org_id=org, scope_id=sid))
    founder_mem = await store.insert(_mem(f"{founder} keynoted the summit.", org_id=org, scope_id=sid))
    # Company row carries the founded edge (subject=founder, object=company);
    # founder row carries a bare mention so the walk can gather it.
    await store.write_links(
        company_mem,
        [EntityLink(founder, "person", "founded", company, "company", 0.9, {})],
    )
    await store.write_links(
        founder_mem,
        [EntityLink(founder, "person", "mentioned", None, None, 0.4, {})],
    )

    from nuvel.memory.backends.postgres_store import _PgGraphView

    graph = _PgGraphView(
        store, org_id=org, user_chain_tags=[f"user:{sid}", f"org:{org}"], limit=20
    )
    out = await relational.relational_recall(f"Who founded {company}", graph)
    ids = {r.id for r in out}
    assert company_mem in ids
    assert founder_mem in ids


@db
@pytest.mark.asyncio
async def test_db_scope_isolation_across_orgs():
    store = await _factory()
    uid = uuid.uuid4().hex[:6]
    name = f"Acme-{uid}"
    org_a, org_b = f"orgA-{uid}", f"orgB-{uid}"

    a_mem = await store.insert(_mem(f"{name} deal in org A.", org_id=org_a, scope_id="ua"))
    b_mem = await store.insert(_mem(f"{name} deal in org B.", org_id=org_b, scope_id="ub"))
    for mid in (a_mem, b_mem):
        await store.write_links(
            mid, [EntityLink(name, "company", "mentioned", None, None, 0.4, {})]
        )

    from nuvel.memory.backends.postgres_store import _PgGraphView

    graph_a = _PgGraphView(
        store, org_id=org_a, user_chain_tags=[f"user:ua", f"org:{org_a}"], limit=20
    )
    out = await relational.relational_recall(f"Tell me about {name}", graph_a)
    ids = {r.id for r in out}
    assert a_mem in ids
    assert b_mem not in ids  # org B's row must never leak into org A's recall
