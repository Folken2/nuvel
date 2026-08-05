"""Hybrid RRF + bounded floor-gated boost cascade for OrgMemoryService.

This is a Python/SQL reimplementation of gbrain's hybrid retrieval algorithm
(RRF fusion, cosine blend, post-fusion boost cascade, autocut, dedup), adapted
to Nuvel's org-scoped memory model. The SQL keyword + vector arms live in
``postgres_store.py``; everything here is pure, side-effect-free ranking logic
so it unit-tests in isolation without a database.

Nuvel's differentiator vs gbrain: there is a scope hierarchy, so the cascade's
first stage is a *tier boost* (user > team > … > org) rather than gbrain's
compiled-truth authority boost. The rest of the cascade — recency, access
count, exact-title match — mirrors gbrain's bounded, floor-gated stages.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Iterable

from nuvel.memory.store import MemoryRow

# gbrain's RRF constant: score = sum(1 / (RRF_K + rank)). See hybrid.ts.
RRF_K = 60

# Final-score blend weights: 0.7 * normalized_rrf + 0.3 * cosine.
RRF_WEIGHT = 0.7
COSINE_WEIGHT = 0.3

# Relational-recall arm weight in the fusion. The graph arm is high-precision /
# low-recall — a resolved typed edge is a strong signal but rare — so it's fused
# UNDER the keyword+vector arms (which carry weight 1.0): it can surface a new
# candidate and reinforce a shared one, but not leapfrog a strong dual-arm hit.
RELATIONAL_WEIGHT = 0.5

# Post-fusion boost bounds. Each metadata factor is clamped so no single signal
# can catastrophically flip rankings (gbrain keeps factors in ~[1.0, 1.6]).
RECENCY_HALFLIFE_DAYS = 30.0
RECENCY_COEFF = 0.4          # → recency factor in [1.0, 1.0 + coeff] ⊂ [1.0, 1.4]
ACCESS_K = 0.15             # → 1 + k*ln(1+count)
ACCESS_MAX = 1.6
TITLE_BOOST = 1.25          # exact-title-match multiplier

# Floor-ratio gate: metadata boosts only apply to results scoring at or above
# top_score * FLOOR_RATIO, so a weak-overlap result can't leapfrog a strong hit
# on metadata alone. gbrain leaves this undefined by default; Nuvel enables it.
FLOOR_RATIO = 0.6

# Autocut: trim the ranked list at the largest normalized score discontinuity.
AUTOCUT_JUMP = 0.2
AUTOCUT_MIN_KEEP = 1


def _key(row: MemoryRow) -> str:
    """Identity for fusion/dedup: prefer the DB id, fall back to content hash."""
    if row.id:
        return f"id:{row.id}"
    return "h:" + _content_hash(row.content)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()


def reciprocal_rank_fusion(
    arms: Iterable[list[MemoryRow]],
    rrf_k: int = RRF_K,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """Fuse ranked arms via RRF and normalize to [0, 1] by the observed max.

    Each arm is a list ordered best-first (rank 0 = top). A result's raw score
    is ``sum(weight_arm / (rrf_k + rank))`` across every arm it appears in.
    ``weights`` (one per arm, default all 1.0) lets a lower-precision arm — e.g.
    relational recall — contribute less per rank than the keyword/vector arms.
    """
    arms = list(arms)
    raw: dict[str, float] = {}
    for i, arm in enumerate(arms):
        w = weights[i] if weights is not None else 1.0
        for rank, row in enumerate(arm):
            raw[_key(row)] = raw.get(_key(row), 0.0) + w / (rrf_k + rank)
    if not raw:
        return {}
    top = max(raw.values())
    if top <= 0:
        return {k: 0.0 for k in raw}
    return {k: v / top for k, v in raw.items()}


def compute_floor_threshold(scores: Iterable[float], floor_ratio: float) -> float:
    """Single gate for the whole cascade: ``max(finite scores) * floor_ratio``.

    Returns ``-inf`` (no gate) for an out-of-range ratio or a non-positive top,
    mirroring gbrain's ``computeFloorThreshold``.
    """
    if not (0.0 <= floor_ratio <= 1.0):
        return float("-inf")
    top = float("-inf")
    for s in scores:
        if math.isfinite(s) and s > top:
            top = s
    if not math.isfinite(top) or top <= 0:
        return float("-inf")
    return top * floor_ratio


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def recency_factor(
    row: MemoryRow,
    now: datetime,
    halflife_days: float = RECENCY_HALFLIFE_DAYS,
    coeff: float = RECENCY_COEFF,
) -> float:
    """Log-scaled half-life recency boost in ``[1.0, 1.0 + coeff]``.

    Uses ``custom_metadata['last_accessed_at']`` when present (a memory that was
    recently *retrieved*), else falls back to ``created_at``. Missing/undated
    rows get a neutral 1.0.
    """
    ts = _parse_dt(row.custom_metadata.get("last_accessed_at")) or row.created_at
    if ts is None or halflife_days <= 0:
        return 1.0
    days_old = max(0.0, (_as_utc(now) - _as_utc(ts)).total_seconds() / 86_400.0)
    component = halflife_days / (halflife_days + days_old)  # (0, 1]
    return 1.0 + coeff * component


def access_factor(
    row: MemoryRow, k: float = ACCESS_K, cap: float = ACCESS_MAX
) -> float:
    """Frequently retrieved memories get ``1 + k*ln(1+count)``, clamped to cap."""
    try:
        count = int(row.custom_metadata.get("access_count", 0))
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return 1.0
    return min(cap, 1.0 + k * math.log1p(count))


def _title_of(row: MemoryRow) -> str:
    title = row.custom_metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title
    # No explicit title → first non-empty line of the content.
    return row.content.strip().splitlines()[0] if row.content.strip() else ""


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def title_factor(query: str, row: MemoryRow, factor: float = TITLE_BOOST) -> float:
    """Exact (normalized) title match → ``factor``, else 1.0."""
    q = _norm(query)
    if not q or factor <= 1.0:
        return 1.0
    return factor if _norm(_title_of(row)) == q else 1.0


def dedup_by_content(rows: list[MemoryRow]) -> list[MemoryRow]:
    """Keep the highest-scoring row per content hash, preserving input order."""
    best: dict[str, float] = {}
    for r in rows:
        h = _content_hash(r.content)
        s = r.score if r.score is not None else float("-inf")
        if h not in best or s > best[h]:
            best[h] = s
    seen: set[str] = set()
    out: list[MemoryRow] = []
    for r in rows:
        h = _content_hash(r.content)
        if h in seen:
            continue
        s = r.score if r.score is not None else float("-inf")
        if s == best[h]:
            seen.add(h)
            out.append(r)
    return out


def autocut(
    rows: list[MemoryRow],
    jump_ratio: float = AUTOCUT_JUMP,
    min_keep: int = AUTOCUT_MIN_KEEP,
) -> list[MemoryRow]:
    """Trim a score-sorted list at its largest normalized discontinuity.

    No-ops when fewer than two finite-scored rows exist, when the top score is
    non-positive, or when no gap clears ``jump_ratio``. Never returns empty.
    Mirrors gbrain's ``applyAutocut`` (score-cliff sizing).
    """
    if len(rows) < 2:
        return rows
    scores = [r.score for r in rows if r.score is not None and math.isfinite(r.score)]
    if len(scores) < 2:
        return rows
    top = max(scores)
    if not math.isfinite(top) or top <= 0:
        return rows

    ordered = sorted(scores, reverse=True)
    norm = [s / top for s in ordered]
    min_keep = max(1, min_keep)

    best_gap = -1.0
    best_idx = -1  # cut AFTER ordered[best_idx] → keep best_idx + 1 rows
    for i in range(min_keep - 1, len(norm) - 1):
        gap = norm[i] - norm[i + 1]
        if gap > best_gap:
            best_gap = gap
            best_idx = i

    if best_idx < 0 or best_gap < jump_ratio:
        return rows

    threshold = ordered[best_idx]
    kept = [
        r
        for r in rows
        if r.score is not None and math.isfinite(r.score) and r.score >= threshold
    ]
    return kept or rows


def fuse_and_rank(
    *,
    vector_arm: list[MemoryRow],
    keyword_arm: list[MemoryRow],
    query: str,
    tier_boost: dict[str, float],
    k: int,
    relational_arm: list[MemoryRow] | None = None,
    relational_weight: float = RELATIONAL_WEIGHT,
    now: datetime | None = None,
    floor_ratio: float = FLOOR_RATIO,
    autocut_jump: float = AUTOCUT_JUMP,
    rrf_k: int = RRF_K,
    enable_autocut: bool = True,
) -> list[MemoryRow]:
    """Full hybrid pipeline: RRF fuse → cosine blend → boost cascade → autocut.

    ``vector_arm`` and ``keyword_arm`` are ranked (best-first) candidate lists.
    Each row carries its cosine similarity in ``row.score`` (0.0 when the row
    has no embedding). Rows may appear in multiple arms; they are unified by id
    (or content hash). ``relational_arm`` is the optional knowledge-graph arm
    (typed-edge recall) — fused at ``relational_weight`` (< 1.0) so it adds
    high-precision candidates without overriding strong keyword/vector hits.
    Returns MemoryRow objects with the final blended+boosted score in ``.score``,
    highest first, capped at ``k``.
    """
    now = now or datetime.now(timezone.utc)
    relational_arm = relational_arm or []

    rrf = reciprocal_rank_fusion(
        [vector_arm, keyword_arm, relational_arm],
        rrf_k,
        weights=[1.0, 1.0, relational_weight],
    )

    # Unify candidates; keep the best cosine seen for a row across all arms.
    unified: dict[str, MemoryRow] = {}
    cosine: dict[str, float] = {}
    for row in [*vector_arm, *keyword_arm, *relational_arm]:
        key = _key(row)
        cos = row.score if row.score is not None and math.isfinite(row.score) else 0.0
        cosine[key] = max(cosine.get(key, 0.0), cos)
        unified.setdefault(key, row)

    # Stage 0: base blend + Nuvel tier boost (the primary ranking multiplier).
    for key, row in unified.items():
        base = RRF_WEIGHT * rrf.get(key, 0.0) + COSINE_WEIGHT * cosine.get(key, 0.0)
        tier = tier_boost.get(row.scope_level, 0.5)
        row.score = base * tier

    # Single floor gate computed once, off the post-tier base scores.
    floor = compute_floor_threshold((r.score or 0.0 for r in unified.values()), floor_ratio)

    # Cascade of bounded, floor-gated metadata boosts (recency → access → title).
    for row in unified.values():
        if row.score is None or not math.isfinite(row.score) or row.score < floor:
            continue
        row.score *= recency_factor(row, now)
        row.score *= access_factor(row)
        row.score *= title_factor(query, row)

    ranked = sorted(
        unified.values(),
        key=lambda r: r.score if r.score is not None else float("-inf"),
        reverse=True,
    )
    ranked = dedup_by_content(ranked)
    if enable_autocut:
        ranked = autocut(ranked, jump_ratio=autocut_jump)
    return ranked[:k]
