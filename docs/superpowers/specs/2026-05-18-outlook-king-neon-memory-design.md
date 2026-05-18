# Outlook-King Neon Memory — Design

**Status:** Approved
**Date:** 2026-05-18
**Scope:** `generated-agents/outlook-king` only

## Problem

`outlook-king` currently stores long-term memory as markdown files (`memory/AGENT_MEMORY.md` + `memory/topics/*.md`). This works for a single user owning the filesystem but does not scale to enterprise multi-user deployment:

- No per-user isolation — every user writes to the same files.
- No way to answer governance questions ("who is using the agent, how much?").
- No searchability beyond `grep`.

## Goals

1. Multi-tenant memory keyed by user, backed by Postgres on Neon.
2. Implement the ADK `BaseMemoryService` interface so the search seam is ADK-native.
3. Preserve the existing tool surface — the agent's prompt and tool calls do not change.
4. Enable governance queries (top users, usage volume) without compromising row-level data isolation.

## Non-goals (v1)

- pgvector / semantic embeddings — deferred. Postgres FTS is sufficient for the expected corpus size (~50 facts/user).
- Verified AAD/OAuth identity — deferred. Header-trust is sufficient; upgrade path is documented.
- Port to `word-king` / `ppt-king` — separate PR.
- Propagation to the nuvel template — separate PR.
- Auto-ingestion of full sessions (`add_session_to_memory`) — stubbed as a no-op. Curated facts only.
- Background summarization of conversations — future option, hooks into the same `add_session_to_memory` stub.

## Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | `outlook-king` only | Reduce blast radius; prove the design before fan-out |
| Storage | Neon Postgres | Scales organically; lower fixed cost than Supabase |
| Memory model | Curated facts only | Matches existing agent-driven save/recall pattern; low row volume |
| Identity | Surrogate `user_id` + `users` lookup table | Tenant isolation without PII in memory rows; governance via JOIN |
| Identity provenance | `X-User-Email` header from add-in | Header-trust; upgrade to JWT verification is non-breaking |
| Search | Postgres FTS (`tsvector` + GIN) | Native, no extension, sufficient signal for ~50 rows/user |
| Existing markdown | Dropped; no migration | Seed content only; clean cutover |
| Service shape | `NeonMemoryService(BaseMemoryService)` + extra curated methods | ADK-native search seam + curated write path on one class |
| Tool surface | Unchanged — same 5 tool names + signatures | Prompt-level zero-diff for the agent |
| Driver | `psycopg` v3 + `AsyncConnectionPool` | Boring, correct, transaction-friendly |
| Kill-switch | None — clean cutover | Dual-write systems cause more incidents than they prevent |

## Architecture

### Schema

A new `nuvel_memory` Postgres schema contains two tables.

```sql
CREATE SCHEMA IF NOT EXISTS nuvel_memory;

CREATE TABLE nuvel_memory.users (
    user_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL UNIQUE,
    display_name TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE nuvel_memory.memories (
    id          BIGSERIAL PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES nuvel_memory.users(user_id) ON DELETE CASCADE,
    app_name    TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT 'core',
    content     TEXT NOT NULL,
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX memories_user_app_topic_idx ON nuvel_memory.memories (user_id, app_name, topic);
CREATE INDEX memories_fts_idx           ON nuvel_memory.memories USING GIN (fts);
```

Schema lives at `generated-agents/outlook-king/db/001_init_memory.sql`. Applied manually for now; Alembic adopted when the second migration appears.

Notes:
- `fts` is a `STORED` generated column — Postgres recomputes on every insert/update, no trigger or application logic to maintain.
- `topic = 'core'` is the reserved value for what was previously `AGENT_MEMORY.md`. Topic files become non-`core` rows.
- `app_name` is currently always `"outlook-king"` but exists to avoid a migration when other agents adopt the same schema.
- `ON DELETE CASCADE` makes GDPR "delete user" one statement.

### Service Class

New file: `outlook_king/state/memory_service.py`

```python
class NeonMemoryService(BaseMemoryService):
    def __init__(self, pool: AsyncConnectionPool, app_name: str): ...

    # BaseMemoryService interface
    async def add_session_to_memory(self, session: Session) -> None:
        return  # no-op: curated-only model

    async def search_memory(self, *, app_name: str, user_id: str,
                            query: str) -> SearchMemoryResponse:
        """FTS query, ranked by ts_rank, top 10 results."""

    # Curated API (not part of BaseMemoryService)
    async def save(self, user_id: str, content: str,
                   topic: str = "core") -> dict: ...
    async def recall(self, user_id: str,
                     topic: str | None = None) -> dict: ...
    async def update(self, user_id: str, content: str,
                     topic: str = "core") -> dict: ...
    async def forget_topic(self, user_id: str, topic: str) -> dict: ...
    async def stats(self, user_id: str) -> dict: ...

    # Identity
    async def upsert_user(self, email: str,
                          display_name: str | None = None) -> str:
        """INSERT ... ON CONFLICT (email) DO UPDATE SET last_seen_at = now()
        RETURNING user_id. Returns user_id as UUID string."""
```

Every method that touches `memories` includes `WHERE user_id = %(user_id)s` and `AND app_name = %(app_name)s`. There is no code path that queries `memories` without those predicates. All SQL lives inside this class; tools and routes never see raw queries. This is the **logical isolation** boundary.

### Tool Surface

Existing file rewritten: `outlook_king/tools/memory_tools.py`

The five exported tool functions keep their current names and parameters:

- `save_memory(content: str, topic: str = "")`
- `recall_memory(topic: str = "")`
- `update_memory(content: str, topic: str = "")`
- `forget_topic(topic: str)`
- `memory_status()`

Internally each one:
1. Pulls `user_id` from `tool_context.state["user_id"]`.
2. Awaits the corresponding `NeonMemoryService` method.
3. Returns the same dict shape current callers expect.

The agent's prompt, instructions, and tool descriptions are unchanged.

ADK's built-in `load_memory` tool is added to the agent's tool list. It calls `search_memory` directly and gives the agent a free-text search path alongside the topic-based recall. Decision locked: include it. Cost is one line in the tool list; benefit is the agent can answer "what do I know about X?" without needing the right topic slug.

### Identity Propagation

1. **Add-in side** (`addin/src/taskpane/helpers/`): the existing fetch wrapper adds two headers to every backend request:
   - `X-User-Email`: `Office.context.mailbox.userProfile.emailAddress`
   - `X-User-Display-Name`: `Office.context.mailbox.userProfile.displayName` (optional)

2. **Backend dependency** (`backend/main.py`):
   ```python
   async def get_user_id(
       x_user_email: str = Header(...),
       x_user_display_name: str | None = Header(None),
   ) -> str:
       return await _memory_service.upsert_user(x_user_email, x_user_display_name)
   ```
   `Header(...)` makes the header required — missing header returns 422.

3. **Runner construction**: the resolved `user_id` is passed to the ADK `Runner`, which stores it on the session.

4. **Tool access**: a `before_agent_callback` in `plugins/memory_plugin.py` mirrors `session.user_id` into `state["user_id"]` for ergonomic tool access.

### Backend Wiring

`backend/main.py` is extended with a FastAPI `lifespan`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool, _memory_service
    _pool = AsyncConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=10)
    await _pool.open()
    _memory_service = NeonMemoryService(_pool, app_name="outlook-king")
    yield
    await _pool.close()
```

`min_size=1` keeps a warm connection to avoid TLS handshake on the first request. Neon auto-suspends idle endpoints; the pool will reconnect on use.

### File Layout

```
generated-agents/outlook-king/
  db/
    001_init_memory.sql                # NEW
  outlook_king/
    state/
      memory_service.py                # NEW — NeonMemoryService
      memory.py                        # DELETED — markdown reader gone
    tools/
      memory_tools.py                  # REWRITTEN — same names, calls service
    plugins/
      memory_plugin.py                 # REWRITTEN — mirrors user_id into state
  memory/                              # DELETED — directory removed
  backend/main.py                      # MODIFIED — lifespan, dependency, runner wiring
  addin/src/taskpane/helpers/          # MODIFIED — fetch wrapper sends headers
  .env.example                         # MODIFIED — adds DATABASE_URL, drops MEMORY_DIR
  tests/test_memory_service.py         # NEW
```

## Testing

New file: `tests/test_memory_service.py`. Uses `testcontainers-python` to spin up a Postgres 16 container, applies the schema, and runs against it. Skipped on CI if Docker is unavailable; runs locally always.

Coverage:

- `upsert_user` idempotency: same email → same `user_id` across N calls.
- `save` then `recall(topic=None)` returns the saved core content.
- `save(content, topic="X")` then `recall("X")` returns it.
- `search_memory(query="preferences")` matches "user prefers..." via stemming, returns ranked top N.
- **Multi-user isolation**: user A saves; user B's `recall` and `search_memory` return zero rows from A. (The leak test.)
- `forget_topic(user_id, topic)` removes only that topic for that user.
- `ON DELETE CASCADE`: deleting a row from `users` removes the user's memories.

Integration test for the FastAPI dependency:
- Missing `X-User-Email` → 422.
- Valid header → user upserted exactly once across N concurrent requests (no duplicate-insert race).

## Rollout

- Schema is applied to Neon manually (one statement) for v1.
- `DATABASE_URL` added to deployment env. Without it, the backend fails fast at startup (no silent fallback).
- No feature flag, no kill-switch. The markdown backend is deleted in the same PR.
- `outlook-king/memory/` directory is removed. The `MEMORY_DIR` env var becomes unused and is removed from `.env.example`.

## Future Extensions

These are intentionally deferred but the design accommodates each:

- **pgvector embeddings**: add an `embedding vector(1536)` column to `memories` and an embedding pipeline on the write path. No schema breaking change.
- **AAD token verification**: replace `Header(...)` with a JWT-verifying dependency that extracts `preferred_username` from the token. Calls the same `upsert_user`. Zero schema or service change.
- **Session summarization**: implement `add_session_to_memory` to write a one-paragraph summary as a `topic='summary'` row.
- **Port to word-king / ppt-king**: each agent gets its own `app_name` value; same `nuvel_memory` schema. Or run separate Neon databases per agent if isolation is desired.
- **Template propagation**: once the design is proven, lift the service and tools back into the nuvel ADK template so newly scaffolded agents inherit it.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| A future query forgets the `user_id` filter | All SQL is centralized in `NeonMemoryService`; no raw queries elsewhere. Multi-user isolation test guards against regression. |
| Header trust — malicious caller spoofs another user's email | Documented limitation. Upgrade path is JWT verification; same `user_id` surface, no schema impact. |
| Neon cold-start latency on first request | `min_size=1` warm pool; accept brief tail latency after suspension as a Neon serverless tradeoff. |
| FTS recall too weak for "fuzzy" intent | Defer to pgvector when corpus exceeds ~500 rows/user or quality complaints surface. Schema change is additive. |
| Connection pool exhaustion under load | `max_size=10` per process; Neon pooler upstream handles fan-in. Revisit if `pg_stat_activity` shows saturation. |
