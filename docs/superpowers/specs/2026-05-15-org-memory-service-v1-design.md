# OrgMemoryService v1 — Design

**Status:** Approved (brainstorm) — pending implementation plan
**Date:** 2026-05-15
**Owner:** @Folken2

## Context

Nuvel's product thesis is to be the **context/memory layer for businesses** — a digital twin where agents share context across a hierarchy:

```
user → team → division → country → corporate → org
```

ADK's built-in `BaseMemoryService` is flat (`app_name`, `user_id`, `query`) and existing vendor memory APIs (mem0, Supermemory, Vertex Memory Bank) are per-user/per-container. None of them model the org graph, scope inheritance, or governance. That layer is nuvel-native work.

This spec defines **v1** of `OrgMemoryService` — the abstraction, write/read semantics, and the first concrete backend. Promotion governance and skill promotion are deliberately out of scope (separate specs).

## Goals

1. A nuvel-owned memory abstraction that takes a **scope chain**, not a flat container.
2. Hierarchical reads with **inheritance** — an agent acting as a user sees memories at every level of its scope chain, scope-weighted.
3. Hierarchical writes with **scope-write ACLs** — you can only write into a scope you belong to.
4. A pluggable backend interface, with one v1 implementation (Postgres + pgvector on Neon).
5. ADK integration via subclass of `BaseMemoryService` — no fork.

## Non-Goals (v1)

- Promotion workflow / governance UI (out — separate spec).
- Peer / sibling visibility or per-row ACLs beyond scope inheritance (out — separate spec).
- Skill promotion (out — reuses scope model but is a different system).
- Multi-tenant orgs in a single deployment (out — assume one org root per deployment).
- Migration tooling from external memory vendors (out).

## Approach Considered

| # | Approach | Verdict |
|---|---|---|
| A | Scope as opaque `container_tag` string on top of any vendor memory API | Rejected — client-side rerank per tier; feature ceiling too low |
| B | Denormalized ancestor array in Postgres + pgvector | **Chosen** — single indexed read query, owns ranking math, no vendor coupling |
| C | Graph DB (Neo4j / pgrouting) | Deferred — overkill for v1 hierarchy semantics; revisit when matrix orgs or cross-scope ACLs land |

## Architecture

```
nuvel/memory/
  __init__.py
  scope.py                # Scope, ScopeChain, ScopeResolver
  org_memory_service.py   # OrgMemoryService(BaseMemoryService) — ADK adapter
  store.py                # MemoryStore protocol (backend interface)
  admin.py                # OrgMemoryAdmin — non-ADK ops (move, delete, list)
  backends/
    postgres_store.py     # v1 backend: Postgres + pgvector
```

Boundaries (each unit has one job; consumers don't read internals):

- **`OrgMemoryService`** knows ADK types. Knows nothing about SQL or vector math.
- **`MemoryStore`** (Protocol) knows storage. Knows nothing about ADK types.
- **`ScopeResolver`** knows the org graph. Knows nothing about either.
- **`OrgMemoryAdmin`** wraps a `MemoryStore` for ops that don't fit the ADK contract.

## Data Model

Single table in Postgres (Neon, pgvector extension):

```sql
create extension if not exists vector;
create extension if not exists pg_trgm;

create table org_memories (
  id              uuid primary key default gen_random_uuid(),
  org_id          text not null,                -- tenant root (e.g. "acme")
  scope_level     text not null,                -- "user" | "team" | "division" | ...
  scope_id        text not null,                -- "albert" | "platform-team" | ...
  scope_chain     text[] not null,              -- ["user:albert","team:platform",...,"org:acme"]
  content         text not null,
  embedding       vector(1536),
  source_app      text,
  source_session  text,
  custom_metadata jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index org_memories_chain_gin on org_memories using gin (scope_chain);
create index org_memories_embedding_ivf on org_memories using ivfflat (embedding vector_cosine_ops);
create index org_memories_scope on org_memories (org_id, scope_level, scope_id);
create index org_memories_content_trgm on org_memories using gin (content gin_trgm_ops);
```

`scope_chain` is denormalized at write time — that's what makes inheritance reads a single indexed query.

## Scope Model

```python
class Scope(BaseModel):
    level: str  # e.g. "user", "team", "division", "country", "corporate", "org"
    id: str

    def tag(self) -> str:
        return f"{self.level}:{self.id}"

class ScopeChain(BaseModel):
    scopes: list[Scope]  # ordered leaf → root

    def tags(self) -> list[str]:
        return [s.tag() for s in self.scopes]

    def contains(self, scope: Scope) -> bool:
        return scope in self.scopes
```

Levels are **configurable strings**, not a hardcoded enum. v1 ships with the default ordering `["user","team","division","country","corporate","org"]` but a deployment can use a subset.

## ScopeResolver

```python
class ScopeResolver(Protocol):
    def resolve(self, user_id: str) -> ScopeChain: ...
```

v1 implementation: config-driven (`org_graph.yaml`) for simplicity and testability. Pluggable so an IdP/HRIS sync (Okta, BambooHR) can replace it without touching memory code.

If the resolver returns an empty chain (unknown user), `OrgMemoryService` falls back to a synthetic `user:<id>` leaf only and emits a warn log. **Never refuses the write** — losing memory is worse than scoping it imprecisely.

## Write Semantics

`OrgMemoryService.add_events_to_memory(*, app_name, user_id, events, session_id=None, custom_metadata=None)`:

1. Resolve `user_chain = ScopeResolver.resolve(user_id)`.
2. Determine target scope:
   - Default: the **leaf** of `user_chain` (the user scope).
   - Override: `custom_metadata["scope"] = {"level": ..., "id": ...}`.
3. **Write-time ACL:** if the target scope is not in `user_chain`, raise `ScopeAuthorizationError`. (You can only write into a scope you belong to.)
4. Compute `scope_chain` for the target scope (its ancestors up to org root). Note: a memory written at `team:platform` has chain `[team:platform, division:eu, …, org:acme]` — **no user prefix**. This is what makes team memory shared across the team.
5. Embed `content`, insert row.

`add_session_to_memory(session)` distills the session into memory entries (v1: simple — one entry per assistant turn with `content = last assistant message + relevant user turn`; same write path as above).

`add_memory(*, app_name, user_id, memories, custom_metadata=None)` writes provided `MemoryEntry`s directly; same scope resolution + ACL logic.

## Read Semantics

`OrgMemoryService.search_memory(*, app_name, user_id, query) -> SearchMemoryResponse`:

1. `user_chain = ScopeResolver.resolve(user_id)`.
2. `q_embedding = embedder.embed(query)`.
3. Single SQL (tier boost rendered as a `CASE` expression from the Python config dict at query time):

```sql
select id, content, scope_level, scope_chain, custom_metadata,
       (1 - (embedding <=> $1)) *
       case scope_level
         when 'user'      then 1.0
         when 'team'      then 0.9
         when 'division'  then 0.75
         -- ... rendered from tier_boost dict
         else 0.5
       end as score
from org_memories
where org_id = $org
  and scope_chain && $user_chain_tags
order by score desc
limit $k;
```

4. `tier_boost` is a config dict, defaults: `{user: 1.0, team: 0.9, division: 0.75, country: 0.7, corporate: 0.65, org: 0.6}`. Tunable per deployment.
5. Map rows to `MemoryEntry`s.

Lexical fallback: rows with `embedding IS NULL` are scored via `similarity(content, query)` from `pg_trgm` and merged into the result with a fixed multiplier (`0.5`). This is inline in v1, not deferred.

No fanout, no per-tier query, no client-side rerank.

## ADK Integration

```python
class OrgMemoryService(BaseMemoryService):
    def __init__(self, store: MemoryStore, resolver: ScopeResolver, embedder: Embedder): ...
    async def add_session_to_memory(self, session: Session) -> None: ...
    async def add_events_to_memory(self, *, app_name, user_id, events, session_id=None, custom_metadata=None) -> None: ...
    async def add_memory(self, *, app_name, user_id, memories, custom_metadata=None) -> None: ...
    async def search_memory(self, *, app_name, user_id, query) -> SearchMemoryResponse: ...
```

Wired via the existing `nuvel/backends/adk/` runner config — same pattern as session services.

## MemoryStore Protocol

```python
class MemoryStore(Protocol):
    async def insert(self, row: MemoryRow) -> str: ...
    async def search(self, *, org_id: str, user_chain_tags: list[str],
                     q_embedding: list[float], query_text: str, k: int,
                     tier_boost: dict[str, float]) -> list[MemoryRow]: ...
    async def move(self, memory_id: str, new_scope: Scope, new_chain: list[str]) -> None: ...
    async def delete(self, memory_id: str) -> None: ...
    async def list_by_scope(self, scope: Scope, limit: int = 100) -> list[MemoryRow]: ...
```

One implementation in v1: `PostgresStore`. A shared contract test suite (`tests/memory/store_contract.py`) any backend must pass — primes a future Supermemory/mem0 backend without rewriting tests.

## Admin API

`OrgMemoryAdmin` (non-ADK, used by ops/tools and — later — the promotion workflow):

- `move(memory_id, new_scope)` — manual promotion/demotion. Recomputes `scope_chain`.
- `delete(memory_id)`.
- `list_by_scope(scope, limit=100)` — inspection and debugging.

No governance, no approval flow — those are v2.

## Error Handling

| Condition | Behavior |
|---|---|
| Target scope not in user's chain on write | Raise `ScopeAuthorizationError` |
| Unknown user / empty chain | Fall back to `user:<id>` leaf, warn-log, do not refuse write |
| Embedder failure | Insert row with `embedding = NULL`; rely on lexical fallback at read |
| `MemoryStore.insert` fails | Bubble up; ADK callers decide retry policy (existing `resilience_plugin` covers this layer) |

## Testing

- **Unit (`scope.py`):** ScopeChain ordering, `contains`, tag formatting.
- **Unit (`ScopeResolver` config impl):** fixture `org_graph.yaml` — resolve known and unknown users.
- **Unit (`OrgMemoryService`):** write-time ACL (scope outside chain rejected); empty-chain fallback; default-leaf write; metadata-override write.
- **Integration (PostgresStore, real Neon branch):**
  - Seed three users across two teams in one division.
  - Verify read inheritance: user A sees their team's team-scoped memory; doesn't see user B's user-scoped memory; both see division-scoped memory.
  - Verify tier boost: a less-similar user memory outranks a more-similar org memory at the default boosts.
  - Verify lexical fallback for `embedding IS NULL` rows.
- **Contract suite (`store_contract.py`):** parameterized over registered `MemoryStore` impls.

## Open Questions Deferred to v2

- Promotion workflow: who approves a user→team memory promotion? Triggers (usage thresholds, eval scores, manual)? Audit trail?
- Peer ACLs: can a team in division EU see division NA's memories with explicit permission?
- Matrix orgs / dotted-line membership (user in two teams).
- Memory expiration / decay policies.
- Skill promotion (separate but related — reuses `Scope` + `ScopeResolver`).
- Backfill / migration from per-agent memory backends.

## Related

- [[project-nuvel-vision]] — long-term thesis this spec executes against.
- `nuvel/plugins/skill_curator_plugin.py` — seed of the skill-promotion path; not touched in v1.
