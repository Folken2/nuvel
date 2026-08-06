# Hybrid ranking internals

Everything below lives in `nuvel/memory/hybrid.py`, which is deliberately DB-free — no
DB handle, no network, no I/O, no clock dependency it can't override via a `now`
argument — so the whole cascade unit-tests without a database
(`tests/test_memory_hybrid.py` exercises it directly). It is not *side-effect*-free:
`fuse_and_rank` writes the computed score back onto the caller's `MemoryRow` objects
(`hybrid.py:290`, `hybrid.py:299-301` — `row.score = ...` / `row.score *= ...`), so the
rows you pass in are mutated in place. The SQL that produces the keyword and vector
arms lives in `nuvel/memory/backends/postgres_store.py`; this file only ranks
candidates it's handed.

## The three arms and RRF fusion

`postgres_store.search()` (`postgres_store.py:115-158`) runs three ranked
candidate lists concurrently:

- **vector arm** — pgvector cosine-distance nearest-neighbour scan, ordered
  best-first by cosine distance (`postgres_store.py:186-203`)
- **keyword arm** — `to_tsvector`/`websearch_to_tsquery` full-text search with a
  `pg_trgm` similarity tiebreak for short/fuzzy queries, ordered best-first by
  relevance (`postgres_store.py:205-242`)
- **relational arm** — typed-edge recall, optional, see below. This one is **not**
  ordered by relevance: the SQL orders by `created_at desc`, i.e. recency
  (`postgres_store.py:387`), and `relational_recall` then appends results
  seed-first — memories mentioning the resolved seed before memories mentioning its
  walked neighbours (`relational.py:161-179`). RRF still treats its position as a
  rank, so within the relational arm "rank 0" means "most recent memory mentioning
  the seed," not "best match."

`reciprocal_rank_fusion` (`hybrid.py:66-89`) fuses them:

```
raw_score(item) = Σ_arm  weight_arm / (RRF_K + rank_in_arm)
```

`RRF_K = 60` (`hybrid.py:25`) — the constant from gbrain's `hybrid.ts`, carried over
unchanged. It's the RRF damping term: a low `RRF_K` makes rank-1 dominate almost
absolutely; a high one flattens the curve so lower ranks still contribute
meaningfully. 60 sits in the middle of that range and is standard in RRF literature.

The vector and keyword arms are weighted `1.0` each; a `relational_arm`, if present,
is weighted `RELATIONAL_WEIGHT = 0.5` (`hybrid.py:35`, passed through
`fuse_and_rank`'s `weights=[1.0, 1.0, relational_weight]` at `hybrid.py:274`) —
see "The relational arm's weight" below. Raw RRF scores are then normalized to
`[0, 1]` by dividing by the observed max (`hybrid.py:86-89`) — an item that appears
in every arm at rank 0 gets `1.0`; everything else is scaled relative to it.

## The cosine blend

Independently of RRF rank, each candidate carries its own cosine similarity in
`row.score` (`0.0` for a row with no embedding, or when the query has no
embedding at all). `fuse_and_rank` takes the **best** cosine seen for a given
row across all three arms (`hybrid.py:277-284`) and blends it with the
normalized RRF score:

```
base_score = RRF_WEIGHT * normalized_rrf + COSINE_WEIGHT * cosine
```

`RRF_WEIGHT = 0.7`, `COSINE_WEIGHT = 0.3` (`hybrid.py:28-29`). RRF dominates the
blend — rank position across arms is trusted more than a single continuous
similarity number — but cosine still nudges close calls, which matters most when
two rows land at the same rank in different arms.

## The boost cascade, in order

`fuse_and_rank` (`hybrid.py:242-311`) applies four multiplicative stages to
`base_score`, in this order. **The floor gate is computed once, off the post-tier
score, and applies to every stage after tier.**

### Stage 0 — tier boost (unconditional, no floor gate)

```python
tier = tier_boost.get(row.scope_level, 0.5)
row.score = base_score * tier
```

(`hybrid.py:286-290`). Defaults come from the module-level `DEFAULT_TIER_BOOST`
constant in `nuvel/memory/org_memory_service.py` (`org_memory_service.py:34-41`) — a
bare module name, not an attribute on the class; `OrgMemoryService.__init__` reads it
as `tier_boost or DEFAULT_TIER_BOOST` (`org_memory_service.py:59`):

| scope_level | factor |
|---|---|
| user | 1.0 |
| team | 0.9 |
| division | 0.75 |
| country | 0.7 |
| corporate | 0.65 |
| org | 0.6 |

A `scope_level` absent from the table (custom hierarchies) falls back to `0.5` —
deliberately *lower* than the lowest named tier, so an unrecognized level doesn't
accidentally rank as highly as `org`. This is nuvel's structural divergence from
gbrain: gbrain has no scope hierarchy, so its first cascade stage boosts
*compiled-truth authority* instead of scope tier.

### The floor gate

```python
floor = compute_floor_threshold((r.score or 0.0 for r in unified.values()), floor_ratio)
```

Note the two details the snippet preserves: a `None` score coalesces to `0.0` rather
than raising, and the ratio comes from `fuse_and_rank`'s `floor_ratio` parameter
(`hybrid.py:293`), which only *defaults* to the module constant
(`floor_ratio: float = FLOOR_RATIO`, `hybrid.py:252`) — a caller can override it.

`compute_floor_threshold(scores, floor_ratio)` (`hybrid.py:92-106`) returns
`max(finite scores) * floor_ratio`, or `-inf` (no gate at all) when
`floor_ratio` is out of `[0, 1]` or the max score is non-positive/non-finite.
`FLOOR_RATIO = 0.6` (`hybrid.py:48`) — a row must score at least 60% of the
current top (post-tier) score to receive *any* of the recency/access/title
boosts below. This is what stops a weak keyword-overlap-only row from
leapfrogging a strong dual-arm hit purely on metadata.

### Stage 1 — recency

```python
recency_factor(row, now) -> [1.0, 1.0 + RECENCY_COEFF]  # ⊂ [1.0, 1.4]
```

`RECENCY_HALFLIFE_DAYS = 30.0`, `RECENCY_COEFF = 0.4` (`hybrid.py:39-40`).
Log-scaled half-life: `component = halflife / (halflife + days_old)`, giving
`1.0` (fully decayed) as `days_old → ∞` and `1.0 + coeff` (`1.4`) at `days_old = 0`.
Prefers `custom_metadata['last_accessed_at']` over `created_at` — a memory that
was *recently retrieved* counts as fresh even if it's old, which matters for
frequently-referenced org policy that never changes but keeps getting read.

### Stage 2 — access frequency

```python
access_factor(row) -> min(ACCESS_MAX, 1.0 + ACCESS_K * ln(1 + access_count))
```

`ACCESS_K = 0.15`, `ACCESS_MAX = 1.6` (`hybrid.py:41-42`). Log-scaled so the
100th access doesn't dwarf the 10th; capped so an extremely hot row still can't
run away with the ranking.

### Stage 3 — exact title match

```python
title_factor(query, row) -> TITLE_BOOST (1.25) if normalized(title) == normalized(query) else 1.0
```

`TITLE_BOOST = 1.25` (`hybrid.py:43`). Title is `custom_metadata['title']` if set,
else the first non-empty line of content (`hybrid.py:157-162`). A query that is
*exactly* a memory's title (case/whitespace-insensitive) gets a flat, bounded
bump — this is a precision signal, not a fuzzy-match one.

All three factors compound multiplicatively on the post-tier, floor-gated score
(`hybrid.py:296-301`) — their combined ceiling on a qualifying row is
`1.4 * 1.6 * 1.25 ≈ 2.8×`, still bounded, never unbounded.

## Why every factor is bounded

Every *metadata* factor — Stages 1-3 — lives in roughly `[1.0, 1.6]` (gbrain's own
convention, carried over deliberately): recency `[1.0, 1.4]`, access `[1.0, 1.6]`,
title `1.0` or `1.25`. Stage 0 is the exception and runs the other way: the tier boost
is `[0.5, 1.0]` (`user` = `1.0` down to the `0.5` unknown-scope fallback), so it is
always a demotion, never an amplification — the highest-tier row keeps its base score
and everything below it is scaled down. An unbounded metadata multiplier could
let a single strong signal — an absurd access count, a stale-but-frequently-hit
row — override the primary keyword+vector+tier relevance signal entirely. Bounding
each factor, and gating the whole cascade behind the floor threshold, keeps
metadata boosts as tie-breakers among already-relevant results rather than a
second, competing ranking system.

## The relational arm's weight

`RELATIONAL_WEIGHT = 0.5` (`hybrid.py:35`) is deliberately below the keyword and
vector arms' `1.0`. The relational arm is high-precision (a resolved typed edge is
a strong signal) but low-recall (most queries don't name a known entity in a
relational shape), so weighting it at parity would let a single relational hit
overwhelm a query where the keyword/vector arms found many weaker matches. At
`0.5` it can introduce a new candidate or reinforce a candidate the other arms
already found, but it can't leapfrog a strong dual-arm hit on its own.

## Dedup

`dedup_by_content(rows)` (`hybrid.py:177-195`) collapses rows sharing a
normalized content hash (`hashlib.sha256` of `content.strip().lower()`), keeping
the highest-scoring instance and preserving input order otherwise. This runs
*after* the boost cascade and *before* autocut, so dedup never discards the
better-scored copy of a duplicate.

## Autocut — score-cliff sizing

```python
autocut(rows, jump_ratio=AUTOCUT_JUMP, min_keep=AUTOCUT_MIN_KEEP)
```

`AUTOCUT_JUMP = 0.2`, `AUTOCUT_MIN_KEEP = 1` (`hybrid.py:51-52`). Rather than
always returning a fixed top-`k`, `autocut` (`hybrid.py:198-239`) normalizes the
sorted scores to `[0, 1]` by the top score, finds the single largest gap between
consecutive normalized scores (scanning from `min_keep - 1` onward so at least
`min_keep` rows always survive), and cuts there **only if that gap clears
`jump_ratio`**. No qualifying gap, fewer than two finite-scored rows, or a
non-positive top score → no-op, return everything. It never returns an empty
list when the input wasn't empty. This is "keep results as long as they look
related to the top hit, stop at the first big drop" rather than "always return
exactly 10."

## Tuning guide

| Symptom | Likely knob | Why |
|---|---|---|
| Results are too few / autocut trims too aggressively | Raise `AUTOCUT_JUMP` (e.g. `0.2 → 0.3`), or pass `enable_autocut=False` to `fuse_and_rank` | A higher jump ratio requires a bigger cliff before cutting, so more borderline results survive. Disabling autocut falls back to a plain top-`k`. |
| An irrelevant high-tier memory dominates | Lower that tier's entry in `tier_boost` (e.g. drop `org` below `0.6`), or raise `FLOOR_RATIO` so metadata boosts can't rescue a weak-relevance high-tier row | Tier boost is Stage 0 and unconditional — it's the biggest lever on ranking. A high-tier row with a mediocre RRF/cosine score can still edge out a lower-tier, high-relevance one; narrowing the tier gap corrects that. |
| Vector arm swamps keyword arm (or vice versa) | Adjust `RRF_WEIGHT` / `COSINE_WEIGHT` (the blend, not the arm weights — RRF fusion itself always treats both arms equally at weight `1.0`) | RRF fusion is rank-based and arm-symmetric by construction; the *blend* stage is where cosine similarity (inherently vector-arm-flavored) gets its own say. Lowering `COSINE_WEIGHT` mutes the vector arm's extra influence beyond its RRF rank; raising it does the opposite. |
| A relational hit is overriding stronger keyword/vector results | Lower `RELATIONAL_WEIGHT` below `0.5` | The relational arm is meant to reinforce, not dominate; if it's still winning at `0.5`, the query set may be over-relying on typed-edge coverage. |
| Recently-added-but-irrelevant memories rank too high | Lower `RECENCY_COEFF` toward `0.0`, or shorten `RECENCY_HALFLIFE_DAYS` so the boost decays faster | Recency is Stage 1, floor-gated — it can't rescue an irrelevant row from below the floor, but among already-relevant rows it can still tip close calls toward "new" over "correct." |
