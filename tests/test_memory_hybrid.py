"""Unit tests for the hybrid RRF + boost-cascade retrieval pipeline.

These exercise the pure ranking logic in ``nuvel.memory.hybrid`` directly, so
they run without a Postgres instance (the SQL arms are covered by the live
contract tests gated on NUVEL_MEMORY_TEST_DSN). Each MemoryRow's ``.score``
carries the arm's cosine similarity going in, and the final blended+boosted
score coming out — same shape in and out.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nuvel.memory import hybrid
from nuvel.memory.store import MemoryRow

TIER = {"user": 1.0, "team": 0.9, "org": 0.6}
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _row(
    content: str,
    *,
    id: str,
    scope_level: str = "user",
    scope_id: str = "u1",
    cosine: float = 0.0,
    created_at: datetime | None = None,
    meta: dict | None = None,
) -> MemoryRow:
    return MemoryRow(
        id=id,
        org_id="acme",
        scope_level=scope_level,
        scope_id=scope_id,
        scope_chain=[f"{scope_level}:{scope_id}", "org:acme"],
        content=content,
        embedding=None,
        custom_metadata=meta or {},
        created_at=created_at or NOW,
        score=cosine,
    )


# ── RRF fusion ────────────────────────────────────────────────────────────


def test_rrf_fusion_combines_arms_weighted_by_rank():
    a = _row("alpha", id="a")
    b = _row("bravo", id="b")
    c = _row("charlie", id="c")
    # a: rank 0 in vector, rank 2 in keyword → appears in both, high fused score.
    # b: only vector rank 1. c: only keyword rank 0.
    vector = [a, b]
    keyword = [c, b, a]
    fused = hybrid.reciprocal_rank_fusion([vector, keyword])

    # a is the only row present in BOTH arms → strictly highest raw fused score.
    assert fused[hybrid._key(a)] == max(fused.values())
    # Normalized to [0, 1] by the observed max.
    assert abs(max(fused.values()) - 1.0) < 1e-9
    assert set(fused) == {hybrid._key(a), hybrid._key(b), hybrid._key(c)}


def test_fuse_and_rank_returns_memory_rows_ranked():
    top = _row("relevant answer", id="a", cosine=0.9)
    mid = _row("somewhat related", id="b", cosine=0.5)
    out = hybrid.fuse_and_rank(
        vector_arm=[top, mid],
        keyword_arm=[top],
        query="relevant answer",
        tier_boost=TIER,
        k=10,
        now=NOW,
        enable_autocut=False,
    )
    assert all(isinstance(r, MemoryRow) for r in out)
    assert out[0].id == "a"
    assert out[0].score is not None and out[0].score > (out[1].score or 0)


# ── Boost cascade: tier ───────────────────────────────────────────────────


def test_tier_boost_affects_ordering():
    # Identical fused/cosine inputs; only scope_level differs. user > team > org.
    user = _row("policy", id="u", scope_level="user", scope_id="u1", cosine=0.5)
    org = _row("policy2", id="o", scope_level="org", scope_id="acme", cosine=0.5)
    out = hybrid.fuse_and_rank(
        vector_arm=[user, org],
        keyword_arm=[],
        query="policy",
        tier_boost=TIER,
        k=10,
        now=NOW,
        enable_autocut=False,
    )
    assert [r.id for r in out] == ["u", "o"]
    assert out[0].score > out[1].score


# ── Boost cascade: recency ────────────────────────────────────────────────


def test_recency_boost_ranks_recent_higher():
    recent = _row(
        "note recent", id="r", cosine=0.5,
        meta={"last_accessed_at": (NOW - timedelta(days=1)).isoformat()},
    )
    stale = _row(
        "note stale", id="s", cosine=0.5,
        meta={"last_accessed_at": (NOW - timedelta(days=400)).isoformat()},
    )
    out = hybrid.fuse_and_rank(
        vector_arm=[recent, stale],
        keyword_arm=[],
        query="note",
        tier_boost=TIER,
        k=10,
        now=NOW,
        enable_autocut=False,
    )
    assert out[0].id == "r"
    assert out[0].score > out[1].score


def test_recency_factor_is_bounded():
    fresh = _row("x", id="1", meta={"last_accessed_at": NOW.isoformat()})
    ancient = _row("x", id="2", meta={"last_accessed_at": (NOW - timedelta(days=10_000)).isoformat()})
    assert hybrid.recency_factor(fresh, NOW) <= 1.0 + hybrid.RECENCY_COEFF + 1e-9
    assert hybrid.recency_factor(ancient, NOW) >= 1.0


# ── Boost cascade: access count ───────────────────────────────────────────


def test_access_count_boost_and_bound():
    cold = _row("x", id="c", meta={"access_count": 0})
    hot = _row("x", id="h", meta={"access_count": 50})
    assert hybrid.access_factor(cold) == 1.0
    assert 1.0 < hybrid.access_factor(hot) <= hybrid.ACCESS_MAX
    # Never exceed the cap even at absurd counts.
    huge = _row("x", id="H", meta={"access_count": 10**9})
    assert hybrid.access_factor(huge) <= hybrid.ACCESS_MAX


# ── Boost cascade: exact title match ──────────────────────────────────────


def test_exact_title_match_boost():
    titled = _row("body text", id="t", cosine=0.5, meta={"title": "Deploy Runbook"})
    plain = _row("Deploy Runbook mention", id="p", cosine=0.5)
    hit = hybrid.title_factor("deploy runbook", titled)
    miss = hybrid.title_factor("deploy runbook", plain)
    assert hit == hybrid.TITLE_BOOST
    assert miss == 1.0


# ── Floor gate ────────────────────────────────────────────────────────────


def test_floor_gate_stops_weak_result_leapfrogging():
    strong = _row("strong hit", id="s", cosine=0.95)
    # A deep tail of filler so the weak candidate sits at a genuinely low fused
    # rank (below top * floor_ratio), the regime the gate exists to protect.
    filler = [_row(f"filler {i}", id=f"f{i}", cosine=0.4) for i in range(20)]
    # Weak base score, but stacked metadata boosts that WOULD leapfrog if ungated.
    weak = _row(
        "weak hit", id="w", cosine=0.05,
        meta={
            "title": "boostme",
            "access_count": 1000,
            "last_accessed_at": NOW.isoformat(),
        },
    )
    out = hybrid.fuse_and_rank(
        vector_arm=[strong, *filler, weak],
        keyword_arm=[],
        query="boostme",
        tier_boost=TIER,
        k=30,
        now=NOW,
        floor_ratio=0.6,
        enable_autocut=False,
    )
    assert out[0].id == "s", "floor gate must prevent the weak row from leapfrogging"
    # The weak row's stacked boosts are suppressed → it stays in the tail.
    ranks = {r.id: i for i, r in enumerate(out)}
    assert ranks["w"] > ranks["s"]


def test_compute_floor_threshold():
    assert hybrid.compute_floor_threshold([1.0, 0.5, 0.2], 0.6) == 0.6
    # Out-of-range ratio → no gate.
    assert hybrid.compute_floor_threshold([1.0], 5.0) == float("-inf")
    # Non-positive top → no gate.
    assert hybrid.compute_floor_threshold([0.0, -1.0], 0.6) == float("-inf")


# ── Autocut ───────────────────────────────────────────────────────────────


def test_autocut_trims_at_score_gap():
    rows = [
        _row("a", id="a"),
        _row("b", id="b"),
        _row("c", id="c"),
        _row("d", id="d"),
    ]
    rows[0].score = 1.0
    rows[1].score = 0.95
    rows[2].score = 0.30  # big cliff here (0.95 → 0.30)
    rows[3].score = 0.25
    kept = hybrid.autocut(rows, jump_ratio=0.2)
    assert [r.id for r in kept] == ["a", "b"]


def test_autocut_noop_without_cliff():
    rows = [_row("a", id="a"), _row("b", id="b"), _row("c", id="c")]
    for r, s in zip(rows, [1.0, 0.98, 0.96]):
        r.score = s
    assert hybrid.autocut(rows, jump_ratio=0.2) == rows


def test_autocut_never_empty():
    rows = [_row("a", id="a")]
    rows[0].score = 0.5
    assert hybrid.autocut(rows) == rows


# ── Dedup ─────────────────────────────────────────────────────────────────


def test_dedup_keeps_highest_scoring_duplicate():
    hi = _row("same content", id="hi")
    lo = _row("same content", id="lo")
    other = _row("different", id="o")
    hi.score = 0.9
    lo.score = 0.3
    other.score = 0.5
    out = hybrid.dedup_by_content([hi, other, lo])
    ids = [r.id for r in out]
    assert "hi" in ids and "lo" not in ids
    assert "o" in ids


def test_fuse_and_rank_dedups_across_arms():
    # Same content, two ids; both arms surface it. Only the best survives.
    v = _row("dup body", id="v", cosine=0.9)
    kw = _row("dup body", id="kw", cosine=0.1)
    out = hybrid.fuse_and_rank(
        vector_arm=[v],
        keyword_arm=[kw],
        query="dup",
        tier_boost=TIER,
        k=10,
        now=NOW,
        enable_autocut=False,
    )
    bodies = [r.content for r in out]
    assert bodies.count("dup body") == 1


# ── Scope isolation (pipeline honors what the arms hand it) ────────────────


def test_pipeline_only_ranks_supplied_scope_rows():
    # The SQL arms enforce scope_chain isolation; the pipeline must not conjure
    # rows outside what it was given. In-chain rows in, in-chain rows out.
    a = _row("in chain", id="a", scope_level="team", scope_id="platform", cosine=0.7)
    out = hybrid.fuse_and_rank(
        vector_arm=[a],
        keyword_arm=[],
        query="in chain",
        tier_boost=TIER,
        k=10,
        now=NOW,
        enable_autocut=False,
    )
    assert {r.id for r in out} == {"a"}
    assert all(r.scope_chain[-1] == "org:acme" for r in out)


# ── Backward compat ───────────────────────────────────────────────────────


def test_returns_memory_rows_with_scores():
    rows = [_row("x", id=str(i), cosine=0.5 - i * 0.1) for i in range(3)]
    out = hybrid.fuse_and_rank(
        vector_arm=rows,
        keyword_arm=[],
        query="x-query",
        tier_boost=TIER,
        k=2,
        now=NOW,
    )
    assert len(out) <= 2
    for r in out:
        assert isinstance(r, MemoryRow)
        assert r.score is not None
