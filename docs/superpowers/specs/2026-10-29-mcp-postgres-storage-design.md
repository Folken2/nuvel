# nuvel-mcp: Postgres storage (Neon) — design

Status: approved for implementation (2026-10-29). The user handed the design
decisions to the builder; every decision below marked *(builder's call)* was
made without a review round and is open to change in the PR.

## Intent

The hosted Skills MCP (Railway service `nuvel`) keeps all of its state as files
on a Railway volume: the OAuth store with clients and per-tester URL keys
(`/data/oauth.json`), run traces (`/data/feedback/_runs/…/*.jsonl`) and recipe
signals and recipes (`/data/feedback/_recipes/…`). A volume ties the service to
one replica and one region, cannot be queried, and is lost with the service.

Postgres becomes the default production store for **all** server state, and a
new **usage** record (who called what, when, how it went) is added so the beta
can be measured. Neon is the target, but nothing is Neon-specific: any Postgres
13+ works, including Railway Postgres.

## Goals

1. One setting, `NUVEL_MCP_DATABASE_URL`, moves every store to Postgres: OAuth
   (clients, codes, access/refresh tokens, URL keys), run traces, recipe signals,
   recipes, and usage.
2. A relational, queryable data model with real columns for the fields people
   filter and group on, plus the full normalised record as `jsonb`.
3. Schema managed by versioned, forward-only SQL migrations, applied
   automatically at server start (and by `nuvel-mcp db migrate`), safe with
   several replicas starting at once.
4. Usage recorded for every JSON-RPC request, off the request path (a batching
   background writer), never failing or slowing a request.
5. A one-shot importer from the existing files (`nuvel-mcp db import`), so the
   live service keeps its clients, URL keys, traces and recipes.
6. `pip install nuvel-mcp` keeps **zero runtime dependencies**: the driver is
   imported only when a database is configured.

## Non-goals

- Moving `record_feedback` outcomes and `propose_improvement` queues (the local
  feedback store). They are off over HTTP and stay local-only.
- Moving MCP sessions out of memory. They are short-lived, and a client
  re-initialises on `404 Session not found` as the MCP spec requires.
- Dashboards or a usage API. The model ships with two SQL views; reporting is
  SQL for now.
- Automatic retention or partitioning. The operator prunes with SQL (documented).
- A down-migration path.

## Anti-goals

- A database secret in a log line, an error returned to a caller, the spec,
  tests or docs. The DSN is redacted wherever it could surface; docs and tests
  use `$NUVEL_MCP_DATABASE_URL`.
- Usage recording that can take a request down: a slow or dead database must
  only cost dropped usage rows (counted and logged), never a failed tool call.
- Storing caller-controlled free text in usage: tool and method names are
  recorded only when they are known to the server; anything else is `unknown`.

## Constraints

- Python 3.11+, stdlib-only by default (`dependencies = []`, no extras), per the
  monorepo convention.
- The HTTP server is thread-per-connection (up to `NUVEL_MCP_MAX_CONNECTIONS`):
  stores must be thread-safe and share a bounded connection pool.
- Neon's pooled endpoint (`-pooler`) runs PgBouncer in transaction mode: no
  session state may be relied on (no `SET search_path`, no session advisory
  locks, no server-side prepared statements).
- The OAuth provider manipulates the store as a dict of tables inside
  `reading()` / `transaction()`; that interface stays, so the provider and its
  tests are unchanged.

## Decisions

### Configuration and selection

| Setting | Meaning |
|---|---|
| `NUVEL_MCP_DATABASE_URL` | Postgres connection string. Set ⇒ Postgres is the default for every store. |
| `NUVEL_MCP_DATABASE_SCHEMA` | Schema the tables live in. Default `nuvel_mcp`. Must match `[a-z_][a-z0-9_]{0,62}`. |
| `NUVEL_MCP_DATABASE_POOL_MAX` | Max pooled connections. Default 10 *(builder's call: fits Neon's free-tier limits behind the pooler)*. |
| `NUVEL_MCP_DATABASE_AUTO_MIGRATE` | `0` turns off migrations at server start. Default on. |
| `NUVEL_MCP_USAGE` | `0` turns off usage recording. Default on when a database is configured. |

- `--trace-store` and `--recipe-store` gain the values `auto` (new default) and
  `postgres`. `auto` = `postgres` when `NUVEL_MCP_DATABASE_URL` is set, else
  `local`. `local` and `module:factory` behave as before.
- The OAuth store is Postgres whenever `NUVEL_MCP_DATABASE_URL` is set, for both
  `serve` and the `client` / `token` commands. `NUVEL_MCP_OAUTH_STORE` is then
  ignored and the startup log says so *(builder's call: the Dockerfile sets that
  variable, so a warning would fire on every boot; an info line is enough)*.
- Without `NUVEL_MCP_DATABASE_URL` nothing changes: files, and no usage records.
- Driver: `psycopg` 3 with `psycopg_pool`, imported lazily. A missing driver
  with a database configured stops startup (exit 2) with the install command
  `pip install "psycopg[binary,pool]"`. The Docker image installs it.

### Connection handling

- One `Database` per process: a `psycopg_pool.ConnectionPool` (min 1, max
  `NUVEL_MCP_DATABASE_POOL_MAX`, health-checked on checkout so Neon's
  autosuspend is survived), `prepare_threshold=None` (PgBouncer-safe),
  `connect_timeout` 10 s, pool wait 10 s.
- Every table is schema-qualified in SQL (the schema name is validated, then
  interpolated as a quoted identifier); no `search_path`.
- `redact_dsn()` replaces the password (and any `password=` keyword) in any
  string before it is logged or raised to a caller.

### Migrations

- Files `nuvel_mcp/db/migrations/NNNN_<name>.sql`, shipped as package data,
  applied in order. `{schema}` in a file is replaced with the quoted schema.
- Bookkeeping: `<schema>.schema_migrations(version int primary key, name text,
  applied_at timestamptz)`.
- Each migration runs in its own transaction under
  `pg_advisory_xact_lock` keyed on the schema, re-checking whether it was
  applied after taking the lock, so concurrent replicas apply it once
  (transaction-scoped locks work behind PgBouncer).
- Applied automatically by `serve` when the database is configured (unless
  `NUVEL_MCP_DATABASE_AUTO_MIGRATE=0`), and by `nuvel-mcp db migrate`.

### Data model (migration 0001)

All timestamps are `timestamptz` in UTC. `tenant` is the OAuth `client_id` of
the caller, `NULL` for unauthenticated/stdio. Tenant columns are plain text, not
foreign keys: history outlives a deleted client.

**OAuth** — five tables with one shape, `id text primary key, client_id text,
record jsonb not null, updated_at timestamptz not null default now()`, plus an
index on `client_id`:

| Table | `id` |
|---|---|
| `oauth_clients` | the `client_id` |
| `oauth_codes` | hash of the authorization code |
| `oauth_access_tokens` | hash of the access token |
| `oauth_refresh_tokens` | hash of the refresh token |
| `oauth_url_keys` | hash of the URL key |

`record` is exactly what the file store keeps for that row (hashes only, never
a secret). A singleton `oauth_state(id = 1, version bigint)` row is the change
counter. *(builder's call: keeping the provider's record shape as `jsonb`
reuses the provider unchanged and its whole test suite; the columns that
matter for operations — id, client — are real columns.)*

**`skill_runs`** — one row per `nuvel.skill_run/1` trace:
`id bigint identity pk, tenant, run_id, install_id, plugin_name,
plugin_version, skill_name, skill_version, host, mode, started_at, duration_s,
outcome, user_corrected, record jsonb, received_at default now()`.
Unique on `(coalesce(tenant, ''), run_id)` — a run is stored once per tenant
(stricter than the file store's per-day check). Indexes:
`(plugin_name, skill_name, started_at)`, `(tenant, started_at)`.

**`recipe_signals`** — one row per `nuvel.recipe_signal/1`:
`id, tenant, signal_id, recipe_id, install_id, plugin_name, category, event,
at, uses, successes, corrections, record jsonb, received_at`. Unique on
`(coalesce(tenant, ''), signal_id)`. Index `(recipe_id, at)`.

**`recipes`** — one row per `nuvel.recipe/2` version:
`id, tenant, recipe_id, version, install_id, plugin_name, category, language,
record jsonb, received_at`. Unique on `(coalesce(tenant, ''), recipe_id,
version)`. The masked free text stays inside `record`, as in the file store.

**`usage_events`** — one row per JSON-RPC request (notifications excluded):
`id bigint identity pk, at, tenant, transport ('http'|'stdio'), method, tool,
outcome ('ok'|'tool_error'|'error'), error_code int, duration_ms int`.
Indexes `(at)`, `(tenant, at)`. `method` is a method the server implements or
`unknown`; `tool` is set only for `tools/call` and is a registered tool name or
`unknown`.

**Views**: `usage_daily` (day, tenant, method, tool, calls, errors, p50/p95
duration) and `skill_runs_daily` (day, tenant, plugin, skill, runs, counts
per outcome — `success`, `partial`, `failure`, and `blocked`/`skipped` as
`not_run` — and corrected runs).

### Stores

- `PostgresTraceStore.append` inserts with `ON CONFLICT DO NOTHING` and returns
  whether a row was written; `query` filters on the real columns, tenant
  `None` = every tenant (as the file store), ordered by `started_at`.
- `PostgresRecipeStore` likewise for signals and recipe versions.
- `PostgresOAuthStore` keeps the `reading()` / `transaction()` contract:
  - `reading()` reads `oauth_state.version` (one round trip) and reuses the
    in-process snapshot when unchanged, so a CLI write on another machine is
    seen on the next request, like the file store's mtime check.
  - `transaction()` locks the state row `FOR UPDATE` (serialising writers
    across replicas), loads a fresh snapshot if the version moved, yields a
    copy, then writes only the rows that changed (upsert / delete), bumps the
    version and commits. An exception rolls everything back.
  - Known limit: the snapshot is the whole OAuth state. Expired codes and
    tokens are already purged by the provider, so it stays small (clients ×
    live tokens); a per-row design is the follow-up if that stops holding.
- Usage: `UsageStore` protocol with `record_batch(events)`. `UsageRecorder`
  wraps it with a bounded queue (10 000) and one daemon thread that writes
  events as they arrive, up to 200 per batched insert (under load, batches
  form while the previous one is being written). Full queue or a
  failed flush drops events, counts them, and logs at most once a minute. It
  flushes on shutdown. With no database there is no recorder.

### Server integration

- `SkillsMCPServer.dispatch` times every request and hands a usage event to
  the recorder, if any; `serve_http` marks the transport `http`.
- `GET /healthz` adds `"storage": "postgres"|"files"`; with Postgres it runs
  `select 1` (2 s budget) and answers `503` with
  `{"status": "error", "error": "database unavailable"}` on failure.
- `serve` logs `Storage: postgres (schema nuvel_mcp)` — never the DSN.

### CLI

- `nuvel-mcp db migrate` — apply pending migrations; print what ran.
- `nuvel-mcp db status` — applied versions and row counts per table.
- `nuvel-mcp db import [--oauth-store PATH] [--feedback-dir DIR]` — copy the
  file stores into Postgres. Traces and recipes are deduped (re-running inserts
  nothing new). OAuth is imported only while `oauth_state.version` is 0 (the
  database has never held OAuth state): afterwards the database is the
  authority, and re-importing would resurrect clients and keys revoked there.
  `--force-oauth` merges anyway (ids already present are left alone). Prints
  counts per table.

### Packaging and deploy

- `pyproject.toml`: package data adds `db/migrations/*.sql`; still no
  dependencies.
- Dockerfile installs `psycopg[binary,pool]` (pinned major) next to the package.
- CI: a `postgres:17` service; the database tests run when
  `NUVEL_MCP_TEST_DATABASE_URL` is set and are skipped otherwise. Each test uses
  its own throwaway schema.
- Docs: deploy guide (Neon on Railway, import, dropping the volume), env-var
  reference, package README.

## Left to the builder

Module layout, SQL formatting, test structure, exact log wording, and how the
CLI prints tables.

## Rollout (operator, after merge)

1. Create the Neon database; set `NUVEL_MCP_DATABASE_URL` on the Railway service
   (and list it with `preserve()` in `.railway/railway.ts`).
2. Deploy: migrations run at start.
3. `railway ssh … -- nuvel-mcp db import` to copy the volume's files.
4. Check `nuvel-mcp db status`, then remove the volume from `railway.ts`.
