# OrgMemoryService — Internal Usage

A scope-aware, hierarchical memory service. ADK 2.x drop-in: subclass of `BaseMemoryService`. Backed by Postgres + pgvector on Neon.

## Components

- `nuvel.memory.scope` — `Scope`, `ScopeChain` value objects (frozen Pydantic).
- `nuvel.memory.resolver` — `ScopeResolver` protocol + `ConfigScopeResolver` (YAML-driven).
- `nuvel.memory.store` — `MemoryRow` dataclass, `MemoryStore` protocol, `ScopeAuthorizationError`.
- `nuvel.memory.embedder` — `Embedder` protocol, `GoogleEmbedder` (text-embedding-004, 768-dim), `NullEmbedder` (returns None — triggers lexical fallback).
- `nuvel.memory.org_memory_service` — `OrgMemoryService(BaseMemoryService)`.
- `nuvel.memory.admin` — `OrgMemoryAdmin` for move/delete/list.
- `nuvel.memory.backends.postgres_store` — `PostgresStore`.
- `nuvel.memory.factory` — `build_default_service()` env-driven factory.

## Enable

Set three env vars and `nuvel run-adk` auto-wires `OrgMemoryService` through ADK's service registry — no custom runner required:

- `NUVEL_ORG_MEMORY_DSN` — Postgres DSN (Neon recommended; pgvector + pg_trgm required).
- `NUVEL_ORG_GRAPH_PATH` — path to `org_graph.yaml` (see `tests/fixtures/org_graph.yaml`).
- `NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default` — opt-in. ADK's `get_fast_api_app` reads this scheme via the registry and constructs the service.
- `GOOGLE_API_KEY` — optional. Without it, embeddings fall back to NULL and reads use `pg_trgm` lexical similarity only.

```bash
export NUVEL_ORG_MEMORY_DSN=postgresql://...
export NUVEL_ORG_GRAPH_PATH=/etc/nuvel/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
nuvel run-adk
```

DB migration runs idempotently on first service instantiation.

### Standalone use (scripts, batch jobs, evals)

If you need `OrgMemoryService` outside the `run-adk` runner, call the factory directly:

```python
from nuvel.memory.factory import build_default_service

svc = await build_default_service()   # reads the same env vars
await svc.add_memory(app_name="x", user_id="alice", memories=[{"content": "..."}])
```

## Write semantics

- Default target scope: the user's leaf scope (resolved by `ScopeResolver`).
- Override via `custom_metadata = {"scope": {"level": "team", "id": "platform"}}` on `add_memory` / `add_events_to_memory`.
- ACL: target scope MUST appear in the user's resolved chain, or `ScopeAuthorizationError` is raised at write time.
- Unknown user: falls back to a `user:<id>`-only chain with a WARNING log. Writes are never refused.
- Embedder failure: row is inserted with `embedding = NULL`; lexical fallback handles reads.

## Read semantics

`search_memory(user_id=..., query=...)` returns memories at any scope in the user's chain, ranked by `cosine_similarity * tier_boost[scope_level]` (or lexical similarity when no embedding).

**Isolation rule:** a row is returned only if its own scope tag (`scope_level:scope_id`) appears in the caller's resolved chain. This is stricter than the original "any scope_chain overlap" approach — it correctly excludes peers' user-scoped memories at shared higher scopes (e.g., Albert never sees Bea's user memories even though they share the same team/division/org).

Default tier boosts:

| Level | Boost |
|---|---|
| user | 1.0 |
| team | 0.9 |
| division | 0.75 |
| country | 0.7 |
| corporate | 0.65 |
| org | 0.6 |

Tunable per deployment via the `tier_boost` kwarg on `OrgMemoryService`.

## Admin ops

```python
from nuvel.memory import Scope
from nuvel.memory.admin import OrgMemoryAdmin

admin = OrgMemoryAdmin(
    store=store,
    chain_for_scope=lambda s: [s.tag(), "org:acme"],  # supply your own
)
await admin.move(memory_id, Scope(level="team", id="platform"))
await admin.delete(memory_id)
rows = await admin.list_by_scope(Scope(level="org", id="acme"))
```

`chain_for_scope` is a callable that returns the full leaf→root chain for a given scope. Wire it from your `ScopeResolver` levels.

## Backends

v1 ships `PostgresStore`. New backends just need to implement the `MemoryStore` protocol and pass `tests/memory/store_contract.py`'s 4 behavioral tests via `make_contract_tests(store_factory)`.

## Tests

| File | Surface |
|---|---|
| `tests/test_memory_scope.py` | Scope, ScopeChain |
| `tests/test_memory_resolver.py` | ConfigScopeResolver (uses `tests/fixtures/org_graph.yaml`) |
| `tests/test_memory_embedder.py` | NullEmbedder |
| `tests/test_memory_org_service.py` | Write ACL, default leaf, metadata override, empty-chain fallback, read shape |
| `tests/test_memory_admin.py` | move/delete/list pass-through |
| `tests/test_memory_factory.py` | factory env handling |
| `tests/test_memory_postgres_store.py` | PostgresStore contract + Postgres-specific tier-boost (skipped without `NUVEL_MEMORY_TEST_DSN`) |
| `tests/test_memory_integration.py` | End-to-end inheritance, isolation, admin-move on real Neon (skipped without DSN) |

Run integration tests against a Neon branch:

```bash
NUVEL_MEMORY_TEST_DSN="postgresql://..." pytest tests/test_memory_postgres_store.py tests/test_memory_integration.py -v
```

## Not in v1

- Promotion workflow / governance UI.
- Peer / sibling ACLs beyond scope inheritance.
- Skill promotion (separate system; reuses Scope).
- Multi-tenant orgs in a single deployment.
- Memory expiration / decay.
