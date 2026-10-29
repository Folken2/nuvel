# Plan: nuvel-mcp Postgres storage

Spec: [2026-10-29-mcp-postgres-storage-design.md](../specs/2026-10-29-mcp-postgres-storage-design.md)

Each phase ends green (`pytest packages/nuvel-mcp/tests`, with and without
`NUVEL_MCP_TEST_DATABASE_URL`) and is one commit.

## Phase 1 — database core and schema

- `nuvel_mcp/db/__init__.py`: settings from env (`DatabaseSettings`), schema-name
  validation, `redact_dsn`, lazy driver import with the install hint,
  `Database` (pool, `ping`, `migrate`, `status`).
- `nuvel_mcp/db/migrations/0001_initial.sql`: every table, index and view in the
  spec.
- `pyproject.toml` package data.
- Tests: settings parsing and validation, redaction, missing-driver error (no
  database needed); migration apply/idempotence/concurrency and status (database).

## Phase 2 — Postgres stores

- `nuvel_mcp/db/stores.py`: `PostgresTraceStore`, `PostgresRecipeStore`,
  `PostgresOAuthStore`, `PostgresUsageStore`.
- `nuvel_mcp/usage.py`: `UsageStore` protocol, `UsageRecorder` (bounded queue,
  batching thread, drop counting, flush on close).
- Tests: trace/recipe append-dedupe-query parity with the file stores; OAuth
  store contract (read caching, cross-instance visibility, rollback, row diff);
  the existing `OAuthProvider` flows run on the Postgres store; recorder
  batching, overflow and failure behaviour with a fake store.

## Phase 3 — wiring

- `--trace-store` / `--recipe-store` `auto` and `postgres`; OAuth store
  selection for `serve` and the `client` / `token` commands; auto-migrate at
  start; storage log line.
- `SkillsMCPServer.dispatch` usage events (known names only); transport tag.
- `/healthz` storage field and database ping.
- `nuvel-mcp db migrate | status | import`.
- Tests: selection matrix, dispatch events, healthz with a dead database,
  importer idempotence from real file stores, end-to-end HTTP with a URL key on
  Postgres.

## Phase 4 — deploy and docs

- Dockerfile installs `psycopg[binary,pool]`.
- CI `postgres:17` service and `NUVEL_MCP_TEST_DATABASE_URL`.
- Deploy guide, env-var reference, package README, `.claude/skills/nuvel/SKILL.md`
  if the CLI surface table lists `nuvel-mcp` commands.
