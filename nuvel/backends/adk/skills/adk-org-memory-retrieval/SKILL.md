---
name: adk-org-memory-retrieval
description: Scope-aware hierarchical memory for ADK agents via OrgMemoryService — wiring it through ADK's service registry with three env vars, plus how retrieval works (hybrid RRF fusion of keyword and vector arms, tier boost, floor-gated boost cascade, autocut, a zero-LLM knowledge graph, relational recall, and answer synthesis). Read when an agent needs memory shared across users/teams/an org rather than per-session, when wiring NUVEL_ORG_MEMORY_URI / NUVEL_ORG_MEMORY_DSN / NUVEL_ORG_GRAPH_PATH, when tuning retrieval quality, or when deciding whether org memory is warranted at all.
---

# Org memory retrieval for ADK agents

## What it is

`OrgMemoryService` (`nuvel/memory/org_memory_service.py`) is an ADK `BaseMemoryService` scoped along a hierarchy — `user > team > … > org` — rather than per session. A fact written once at a higher scope (a team decision, an org policy) is retrievable by every member beneath it in the hierarchy; a fact written at `user` scope stays private to that user. This is the difference between "memory this session remembers" and "memory the org accumulates" — most agents only need the former (ADK's built-in memory is fine), but a team-facing or multi-tenant agent that should get smarter as its whole org uses it needs the latter.

Retrieval is hybrid (SQL keyword + vector), boosted by scope tier, backed by a zero-LLM knowledge graph that self-wires from every write, and can answer relationship questions ("who founded Acme?") without an LLM at all.

## Wiring (three env vars)

There are **two distinct activation paths** — do not conflate them.

**Path 1 — meta-agent / `run_adk.py`, via ADK's service registry.** `nuvel.memory.adk_registry.register_org_memory_scheme()` registers a factory under the `nuvel-org-memory` scheme through ADK's official extension point, `google.adk.cli.service_registry.register_memory_service` (`adk_registry.py:56`). After that, `get_fast_api_app(memory_service_uri="nuvel-org-memory://default")` constructs `OrgMemoryService` natively — the same mechanism ADK uses for its own built-in `agentengine://` and `rag://` schemes. **No monkey-patching.**

`run_adk.py` only calls the registrar when `NUVEL_ORG_MEMORY_URI` is set (`run_adk.py:95-98`); it is passed straight through as `memory_service_uri` (lines 106, 126). The registry's own factory (`adk_registry.py:31-42`) then requires both `NUVEL_ORG_MEMORY_DSN` and `NUVEL_ORG_GRAPH_PATH` to be set — it **raises** `RuntimeError` if either is missing, because at this point the caller has explicitly opted into org memory:

```bash
export NUVEL_ORG_MEMORY_DSN=$NUVEL_ORG_MEMORY_DSN     # postgres DSN, never inline a literal
export NUVEL_ORG_GRAPH_PATH=/path/to/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
```

**Path 2 — generated-agent retrieval backend, `memory/org_backend.py`.** This is a *separate* surface with different behaviour: `build_memory_service()` (`org_backend.py:35`) reads only `NUVEL_ORG_MEMORY_DSN` (`ENV_DSN`, line 26) and **returns `None`** — never raises — when it's unset (line 42), or when the import fails, or when the connection fails (lines 43-58). `harness.py`'s async `memory_service` property (`harness.py.tmpl:185`) caches the built service and hands it to `build_runner(memory_service=...)`. Unset the DSN, or leave the `nuvel.memory` extra uninstalled, and the agent silently keeps using its markdown memory store — it never crashes on a missing DB.

Neither path is on by default. `factory.build_default_service()` (`factory.py:22`) is the third entry point: a standalone async constructor for scripts, batch jobs and evals that don't go through either wiring path.

## Hybrid retrieval

`postgres_store.py`'s `search()` runs two arms concurrently over the same scope-isolated candidate pool: a SQL full-text/trigram **keyword arm** and a pgvector cosine-distance **vector arm** (`postgres_store.py:169-244`). A third, optional **relational arm** (typed-edge recall, below) is fused in alongside them. All three are combined by Reciprocal Rank Fusion:

```
score = Σ_arm  weight_arm / (RRF_K + rank_in_arm)      # RRF_K = 60  (hybrid.py:25)
```

`hybrid.py` holds only pure, side-effect-free ranking math — no DB calls, no I/O — so the whole cascade unit-tests without a database (`hybrid.py:1-13`). See `references/hybrid-ranking.md` for the full formula, every cascade stage, and a tuning guide.

## The tier boost is nuvel's divergence

The boost cascade's **first stage** is a scope-tier boost (`hybrid.py:286-290`): each row's fused RRF+cosine score is multiplied by `tier_boost[row.scope_level]`, defaulting to `{"user": 1.0, "team": 0.9, "division": 0.75, "country": 0.7, "corporate": 0.65, "org": 0.6}` (`org_memory_service.py:34-41`). This is nuvel's actual divergence from gbrain: gbrain has no scope hierarchy, so its equivalent cascade opens with a *compiled-truth authority* boost instead. Nuvel's opens with tier, because "how far down the org chart did this come from" is the dominant relevance signal a scoped memory store has that a flat one doesn't.

Every stage after tier is bounded and **floor-gated**: `compute_floor_threshold` (`hybrid.py:92-106`) computes `max(score) * FLOOR_RATIO` (`FLOOR_RATIO = 0.6`) once, off the post-tier base scores, and only rows scoring at or above that floor receive the recency/access/title multipliers (`hybrid.py:292-301`). Each factor is itself clamped — recency to `[1.0, 1.4]`, access to `[1.0, 1.6]`, title match to a flat `1.25` — so no single metadata signal can catastrophically flip the ranking on its own. An unbounded multiplier could let a stale, low-relevance row with a lucky access count leapfrog a strong keyword+vector hit; the bound plus the floor gate together prevent that.

## Autocut and dedup

Rather than always returning a fixed top-N, `apply_autocut` (`hybrid.py:198-239`, exposed as `autocut`) trims the ranked list at its largest score discontinuity — the point where results stop looking related to the top hit, not an arbitrary count. `dedup_by_content` (`hybrid.py:177-195`) collapses duplicate content (by hash) to the highest-scoring instance before autocut runs. Both are pure and run inside `fuse_and_rank` (`hybrid.py:242-311`) after the boost cascade.

## The knowledge graph self-wires

Every memory write triggers `extract_entity_links` (`extraction.py:151`) over the content text, fire-and-forget (`org_memory_service.py:218-234`), with **zero LLM calls**: verb regexes for typed relationships plus a bare-capitalized-phrase scan for everything else. Precedence — first match wins when a pattern could classify the same edge two ways — is, in source order (`extraction.py:99-121`): `founded > invested_in > advises > partner_of > competitor_of > attended > works_at`, plus a separate `is <Title> at <Company>` employment form checked first (`_WORKS_AT_TITLE`, line 125). Everything not captured by a typed pattern becomes a low-confidence `mentioned` bare edge, unless it reduces to a stopword (`_STOPWORDS`, lines 58-66) — precision-first: better to under-extract than to pollute the graph with "The", "It", or "Yesterday" as entities. Schema lives in `0001_init.sql` and `0002_entity_links.sql`; see `references/knowledge-graph-schema.md`.

## Relational recall

`parse_relational_query` (`relational.py:68`) detects relationship questions — "who founded Acme", "founders of Acme", "who works at Globex" — deterministically: regex only, no LLM, with a bounded (1-80 char) seed capture so it's ReDoS-safe (`relational.py:31`). A match resolves a seed entity against `entity_names`, then walks typed edges via `GraphView.counterparts` to gather memories mentioning the seed and its neighbours (`relational_recall`, `relational.py:119-179`). The arm is fail-open: a non-relational query, an unresolved entity, or any graph error yields an empty arm rather than breaking the keyword+vector hot path (`postgres_store.py:160-167`). It's fused at `RELATIONAL_WEIGHT = 0.5` (`hybrid.py:35`) — under the keyword/vector arms' weight of `1.0` — because it's high-precision but low-recall: a resolved typed edge is a strong signal but rare, so it can surface a new candidate or reinforce a shared one, but never leapfrog a strong dual-arm hit on its own.

## Synthesis and gap analysis

`synthesize` (`synthesis.py:386`) turns the top-N already-ranked rows into a cited prose answer via an injected `SynthesisLLM`, degrading gracefully to a numbered ranked list when no LLM is wired, the call fails, or the response is unparseable (`synthesis.py:376-432`). `analyze_gaps` (`synthesis.py:265`) is the deterministic counterpart — no LLM — flagging stale entities (nothing added past `stale_after_days`, default 30), unknown topics (query terms nothing matched), and contradictions (a negated claim beside its affirmative twin). Both are a **thin pass over rows `hybrid.fuse_and_rank` already produced — they never re-rank or replace search.** Call `search_memory(..., synthesize=True)` on the service to get a `SearchResult` instead of the default `SearchMemoryResponse` (`org_memory_service.py:125-155`).

## Attribution

The retrieval design — RRF fusion, the tier/boost cascade shape, relational recall, zero-LLM link extraction, and answer synthesis over ranked rows — derives from [garrytan/gbrain](https://github.com/garrytan/gbrain) (MIT, © 2026 Garry Tan). These are independent Python/SQL reimplementations of gbrain's algorithm design adapted to nuvel's scope-hierarchy memory model, which gbrain does not have — no gbrain source is vendored or copied. See `THIRD_PARTY.md` for the full module-by-module mapping. Reciprocal Rank Fusion itself is separate prior art: Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, SIGIR 2009.

## When NOT to use

- **A single-user agent.** ADK's built-in per-session memory already covers it; org memory's scope resolution, tier boost, and knowledge graph are pure overhead with nothing to scope across.
- **No Postgres with pgvector available.** Both the keyword and vector arms live in `postgres_store.py`; there's no alternative backend. If you can't run pgvector, this subsystem isn't available to you yet.
- **Latency-critical paths.** A search issues two-to-three concurrent SQL queries plus in-process ranking; that's an extra round trip most sub-100ms budgets can't absorb.
- **A markdown memory store is genuinely enough.** If the agent's memory need is "remember what we discussed last time, for this one user," the generated agent's default markdown store already does that with zero infrastructure. Reach for org memory when the win is *sharing* knowledge across users/teams, not just persisting it for one.

## Quick reference

```bash
# run_adk.py / meta-agent path — service-registry wiring
export NUVEL_ORG_MEMORY_DSN=$NUVEL_ORG_MEMORY_DSN
export NUVEL_ORG_GRAPH_PATH=/path/to/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
```

```python
from nuvel.memory.adk_registry import register_org_memory_scheme
register_org_memory_scheme()  # idempotent; call once at process startup
```

| Concept | API |
|---|---|
| Register the ADK scheme | `register_org_memory_scheme()` (`adk_registry.py`) |
| Standalone build (scripts/evals) | `factory.build_default_service()` |
| Generated-agent backend (degrades to `None`) | `memory/org_backend.py::build_memory_service()` |
| Hybrid search entry point | `OrgMemoryService.search_memory(..., synthesize=False\|True)` |
| Pure ranking math | `nuvel.memory.hybrid.fuse_and_rank` |
| Relational-question detection | `nuvel.memory.relational.parse_relational_query` |
| Zero-LLM extraction | `nuvel.memory.extraction.extract_entity_links` |
| Answer synthesis | `nuvel.memory.synthesis.synthesize` / `analyze_gaps` |

Deeper dives: `references/hybrid-ranking.md` (RRF, the boost cascade, tuning) and `references/knowledge-graph-schema.md` (tables, edge precedence, adding an edge type). Related skills: `adk-memory-self-improvement`, `adk-long-horizon-guardrails`, `adk-cron-isolation`.
