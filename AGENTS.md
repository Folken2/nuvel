# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

nuvel is a toolkit for generating production-ready AI agents across three frameworks:

- Google ADK, the default framework.
- Buzz (Nostr relay, Bitcoin/Lightning tools).
- Hermes Agent (platform gateways).

It ships as:

- Knowledge skills in `nuvel/backends/<framework>/skills/`.
- A CLI scaffolder exposed as `nuvel agent create` (deprecated alias: `nuvel new`).
- A meta-agent exposed as `nuvel run`, implemented as a Google ADK `LlmAgent` in `nuvel/agent.py`.
- A Skills MCP server exposed as `nuvel mcp serve` (stdio or HTTP), in `packages/nuvel-mcp/src/nuvel_mcp/` (stdlib only; `nuvel/mcp/` is a deprecated alias).

Generated projects are written to `generated-agents/<name>/` and are standalone runnable FastAPI apps. They should not depend on importing the local `nuvel` package at runtime.

## Common Commands

```bash
make install     # pip install -e .; exposes the nuvel console script
make test        # python -m pytest tests/ -v
make run         # nuvel run
make dev         # nuvel run --dev
make dev-ui      # adk web with all 10 plugins loaded
make skills      # nuvel skills list
```

Run a single test with:

```bash
pytest tests/test_scaffold.py::test_name -v
```

Tests use `pytest-asyncio` auto mode from `pyproject.toml`; do not add unnecessary `@pytest.mark.asyncio` decorators.

## Architecture Notes

Each backend under `nuvel/backends/<framework>/` follows the same broad shape:

- `scaffold.py` orchestrates template copying and placeholder substitution.
- `templates/` contains the project skeleton copied into generated agents.
- `skills/` contains framework-specific Anthropic-format skills.

ADK also has `templates_overlays/` for optional features such as Slack, Telegram, and Teams gateways.

When adding cross-cutting scaffolding behavior, check all three backends. ADK may implement the behavior while the other frameworks may need explicit rejection or compatibility handling.

## Template Rules

Files under backend `templates/` are text templates, not importable Python modules. Preserve placeholders such as:

- `{{agent_package}}`
- `{{agent_name}}`

Placeholders may appear in file contents and paths. Avoid edits that accidentally resolve or remove them.

## Plugin Chains

Do not confuse the two plugin chains:

- `nuvel/plugins/` is for the nuvel meta-agent itself.
- `nuvel/backends/adk/templates/{{agent_package}}/plugins/` is copied into generated ADK agents.

Changes to the generated-agent template plugin chain affect future scaffolded agents, not the running meta-agent.

## Skills

Skills are progressive-disclosure documentation, not Python code. They live under `nuvel/backends/*/skills/<name>/SKILL.md` and follow the Anthropic skills format.

Generated ADK agents should prefer the built-in `SkillToolset` instead of duplicating custom `list_skills` or `read_skill` FunctionTools.

Keep `.claude/skills/nuvel/SKILL.md` in sync with CLI flags, scaffold behavior, and agent architecture changes because it is the primary skill entry point for users driving coding agents against this repo.

## Skills MCP Server

`nuvel mcp serve` starts a stdlib-only MCP (Model Context Protocol) server that exposes a skills hub — the [Nuvel Skills](https://github.com/Folken2/skills) repo, or any directory laid out the same way — to MCP clients such as Claude Code, Cursor, and Codex. It speaks stdio by default, or HTTP so clients can point at a URL with nothing installed locally.

```bash
nuvel mcp serve [--skills-dir <dir>] [--theme <t>] [--require <cap>]... [--with-org-memory <dsn|path>] [--feedback-dir <dir>]
                [--transport stdio|http] [--host 127.0.0.1] [--port 8080] [--allow-remote]
                [--no-auth] [--require-auth]
nuvel mcp client create --name <n> [--redirect-uri <u>]...
nuvel mcp client list [--json]
nuvel mcp client delete <client-id>
nuvel mcp token revoke <token-or-client-id>
```

- `--skills-dir` (default: current directory) points at a skills hub. It accepts either the skills directory itself (contains `index.json`) or a repo root that contains `skills/index.json`.
- `--transport stdio` (default) speaks JSON-RPC 2.0 over stdio: protocol messages on stdout (one JSON object per line), diagnostics on stderr.
- `--transport http` serves the same dispatcher over HTTP (diagnostics still on stderr; request bodies are never logged). `--host` defaults to `127.0.0.1`; binding a non-loopback address (e.g. `0.0.0.0`) exits with an error unless `--allow-remote` is passed. `--port 0` picks a free port. Every MCP method over HTTP requires an OAuth 2.1 access token (see **Authentication** below).

HTTP endpoints:

| Endpoint | Transport | Behaviour |
| --- | --- | --- |
| `POST /mcp` | Streamable HTTP (2025-03-26) | JSON-RPC request or batch. Replies `application/json`, or `text/event-stream` (SSE-framed) when `Accept` ranks it above JSON. `initialize` returns an `Mcp-Session-Id` header that every later request must echo; a missing/unknown id → `404`. Notifications only → `202`, empty body. |
| `GET /mcp` | Streamable HTTP | `Accept: text/event-stream` + session id → SSE stream for server→client messages, with `: ping` comments every 15s; closed when the session ends. |
| `DELETE /mcp` | Streamable HTTP | Terminates the session → `204`. |
| `GET /sse` | Legacy HTTP+SSE (2024-11-05) | Opens an SSE stream whose first event is `endpoint` with `/messages?sessionId=<id>`. The session lives as long as the stream. |
| `POST /messages?sessionId=<id>` | Legacy HTTP+SSE | JSON-RPC in → `202`; responses arrive on the `/sse` stream. |
| `GET /healthz` | — | `{"status": "ok", "transport": "http", "skills": N, "themes": N, "tools": N}`: counts only, no auth, never the catalog. |
| `GET /.well-known/oauth-protected-resource` | OAuth (RFC 9728) | `resource`, `authorization_servers`, `scopes_supported`, `bearer_methods_supported`. No auth. |
| `GET /.well-known/oauth-authorization-server` | OAuth (RFC 8414) | Issuer, endpoints, `S256`, grant types and client auth methods. No auth. |
| `POST /register` | OAuth (RFC 7591) | Dynamic client registration, rate-limited per IP (20/h) and globally (200/h). No auth. |
| `GET /authorize`, `POST /authorize` | OAuth | Authorization code + PKCE: validates the request, shows a consent page, then sends the user to log in. |
| `GET /oauth/callback` | OAuth | GitHub's redirect back; exchanges GitHub's code for the user's login. |
| `POST /oauth/dev-login` | OAuth | Only with `NUVEL_OAUTH_DEV_LOGIN=1`: a clearly-labelled username-only login for tests. |
| `POST /token` | OAuth | `authorization_code`, `refresh_token` and `client_credentials` grants. |

HTTP security defaults and settings:

- **Origin check (DNS-rebinding protection)** — requests carrying an `Origin` must come from a loopback origin (`http://localhost:*`, `http://127.0.0.1:*`, `[::1]`) or one listed in `NUVEL_MCP_ALLOWED_ORIGINS` (comma-separated, exact match); otherwise `403`. Requests without `Origin` (curl, native clients) are allowed. `OPTIONS` answers CORS preflights, echoing the allowed origin (never `*`) and exposing `Mcp-Session-Id`.
- `NUVEL_MCP_MAX_BODY_BYTES` (default 1 MiB) → `413` above it. `NUVEL_MCP_MAX_SESSIONS` (default 64) → `503` when full. `NUVEL_MCP_SESSION_TTL` (default 3600s) expires idle sessions; an open SSE stream keeps its session alive.
- `install_skill` with `confirm=true` writes to the **server host's** filesystem (`target_dir`, default `$NUVEL_SKILLS_INSTALL_DIR` or `~/.claude/skills`), not the client's. On a shared host, set `NUVEL_SKILLS_INSTALL_DIR` to a scratch path or don't expose the server publicly.

### Authentication (OAuth 2.1)

**Everything over HTTP is gated.** `initialize`, `resources/list`, `resources/read`, `tools/list`, `tools/call` (all nine tools, read-only ones included), the `GET /mcp` and `/sse` streams, and `DELETE /mcp` all require `Authorization: Bearer <access token>`. The only unauthenticated surfaces are `/healthz`, the two `/.well-known/` documents, `/register`, `/authorize`, `/oauth/callback` (and `/oauth/dev-login`), `/token`, and CORS preflights. Without a valid token the server answers `401` with `WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource", scope="skills:read skills:write"` (plus `error="invalid_token"` when a token was presented). That lets MCP clients run discovery → registration → authorization → retry on their own. Every token failure (unknown, expired, revoked, wrong client, wrong resource) gets the same `401`. `403` is reserved for a valid token that isn't allowed: a missing scope, or a user since removed from the allowlist.

The server is its own authorization server, in `packages/nuvel-mcp/src/nuvel_mcp/oauth.py`, stdlib only:

- **Scopes**: `skills:read` covers every MCP method and the read-only tools. `skills:write` covers `install_skill`, `propose_improvement`, `record_feedback`, and any unknown tool, so it fails closed. Clients get both unless they ask for less.
- **Dynamic registration** (`POST /register`) accepts `redirect_uris`, `client_name` and `token_endpoint_auth_method` (`none`, `client_secret_basic` or `client_secret_post`; only confidential clients get a `client_secret`). Redirect URIs must be `http://localhost:*` or `http://127.0.0.1:*`, or listed exactly in `NUVEL_OAUTH_REDIRECT_ALLOWLIST` (comma-separated); anything else is `invalid_redirect_uri`. Registered clients can only use `authorization_code` + `refresh_token`. Because registration is open, `client_credentials` is reserved for operator-issued clients (`nuvel mcp client create`).
- **Authorization code + PKCE**: `S256` only, `state` required, `resource` (RFC 8707) checked if present. The consent page leads to a GitHub login (`NUVEL_OAUTH_GITHUB_CLIENT_ID` / `NUVEL_OAUTH_GITHUB_CLIENT_SECRET`, a GitHub OAuth app whose callback URL is `<origin>/oauth/callback`). The login is then checked against `NUVEL_OAUTH_ALLOWED_USERS` (comma-separated GitHub logins). If that is unset, any GitHub user may log in, and the server warns at startup. The pending login is bound to an HttpOnly `SameSite=Lax` cookie, so a login link opened in another browser can't complete it. Codes are single-use and live 60s. Replaying a code revokes the tokens the first redemption issued.
- **Token endpoint**: access tokens last 1h. Refresh tokens last 30 days and rotate on every use; presenting a rotated-out refresh token revokes its whole grant. `client_credentials` (confidential, operator-issued clients only) returns an access token without a refresh token; this is the path for services such as our own fleet. `resource`, if sent, must name this server (its origin, or `<origin>/mcp` or `<origin>/sse`), and tokens are only accepted for the resource they were issued for.
- **Origin / issuer**: `NUVEL_MCP_PUBLIC_URL` (e.g. `https://skills.example.com`) is the issuer and token audience. It is required for a non-loopback bind. On loopback it defaults to the request's `Host`.
- **Storage**: one JSON file at `$NUVEL_MCP_OAUTH_STORE` (default `~/.nuvel/oauth.json`) holding `clients`, `codes`, `access_tokens` and `refresh_tokens`. Tokens, codes and client secrets are kept only as SHA-256 hashes, with metadata (client, subject, scope, resource, issued/expiry, revoked). The file is mode `0600`, written atomically (temp file + `os.replace`), and its parent dirs are created `0700`. A running server picks up CLI changes on its next request. An unreadable store fails closed. Tokens, codes and verifiers are never logged.
- **Login setup**: with neither GitHub credentials nor `NUVEL_OAUTH_DEV_LOGIN=1` set, `--transport http` refuses to start. `NUVEL_OAUTH_DEV_LOGIN=1` swaps GitHub for a clearly-labelled page that accepts any username; it is for tests and is refused on a non-loopback bind.

Operator escape hatches:

- `--require-auth` (stdio) gates the stdio transport too: every message must be backed by the access token in `NUVEL_MCP_ACCESS_TOKEN`, re-validated against the store per message (expiry and revocation apply mid-session; `resource` is checked when `NUVEL_MCP_PUBLIC_URL` is set). **By default stdio stays ungated.** It is a local subprocess that the user's own MCP client launched, with the user's own privileges and filesystem access, so a token would protect nothing the process can't already reach.
- `--no-auth` (HTTP) disables OAuth for local development. It prints a loud one-line `WARNING` on stderr at startup and is refused unless `--host` is loopback.
- `nuvel mcp client create --name <n> [--redirect-uri <u>]` issues a confidential client (`client_credentials`, plus the code flow when a redirect URI is given) and prints its `client_secret` once; only the hash is stored. `nuvel mcp client list [--json]` shows clients without secrets, and `nuvel mcp client delete <id>` removes one and revokes its tokens.
- `nuvel mcp token revoke <token-or-client-id>` revokes one access or refresh token (a refresh token takes its whole grant with it), or every token issued to a client id.

A service fetches a token with `curl -u "$CLIENT_ID:$CLIENT_SECRET" -d grant_type=client_credentials <origin>/token`.

Point a client at it, e.g. Claude Code: `claude mcp add --transport http nuvel-skills http://127.0.0.1:8080/mcp` (the client runs the OAuth flow itself on the first `401`).

It exposes:

- **Resources** — `skill://{theme}/{name}` for every skill in the hub's `index.json`, returning the full `SKILL.md`.
- **Tools**:
  - `search_skills` — ranked search. Multi-word queries are AND-matched, case-insensitively, against name, description, theme and author (`-`, `_`, `.`, `/` count as spaces, so `installer transaction` finds `installer-transaction-design`). `include_body=true` also searches `SKILL.md` bodies and returns a `snippet`. Optional `theme`, `limit` (default 10, max 50).
  - `list_skills` — paginated browse (`theme`, `limit` default 50 / max 200, `offset`); returns `total` for paging. An unknown theme returns an empty list.
  - `list_themes` — every theme with its skill count and skill names.
  - `get_skill` — full content, frontmatter `metadata` and top-level `version` by `name` or `theme/name`. Optional `section` returns a single `## ` section instead of the whole file. `metadata.template_variables` lists any `{{ var }}` placeholders; with `--with-org-memory` they are resolved against `OrgMemoryService` into `resolved_content`. Every response embeds `health` — the same report `check_skill_health` returns.
  - `install_skill` — copies a skill's directory (`SKILL.md` plus supporting files) into `<target_dir>/<name>/`. `target_dir` defaults to `$NUVEL_SKILLS_INSTALL_DIR`, then `~/.claude/skills`. Plan-first: `confirm=false` (default) is read-only and reports `action` (`install` / `update` / `noop`); `confirm=true` writes through a staging directory, backs an existing copy up to `<dest>.backup` (never overwriting an existing backup), refuses symlinked or non-directory targets (`blocked`), and re-verifies the result. Statuses: `planned`, `installed`, `noop`, `blocked`, `failed`.
  - `fence_value` — wraps a value in `[begin customer-specific data (<source>, unverified)]` … `[end customer-specific data]` markers so a discovered or user-supplied fact can't be read as an instruction.
  - `propose_improvement` — sends a skill-improvement proposal (`current_version` is optional and read from frontmatter when omitted). Every call returns a `feedback_id` and a `delivery` field.
  - `record_feedback` — records the outcome of using a skill (`outcome`: success / partial / failure / blocked; `severity`: blocking / misleading / minor; optional `section`, `what_worked`, `proposed_patch`, …) in the feedback store.
  - `check_skill_health` — a skill's report from the feedback store: 30-day outcome counts, `trend`, `flagged_sections` (≥2 reports on a section with a blocking or misleading one), `queued_proposals` with the newest `proposals`, and a `recommendation` (`ok` / `review_before_use` / `use_cautiously`, driven by outcome reports).

`--theme <t>` scopes discovery (`resources/list`, `search_skills`, `list_skills`, `list_themes`, `get_skill`, `install_skill`) to one theme; `resources/read` stays unscoped. `--require <cap>` (repeatable, comma-separated) further limits `resources/list`, `search_skills`, `list_skills` and `list_themes` to skills whose `requires` frontmatter declares every listed capability.

`propose_improvement` delivers via the first available path:

1. `GITHUB_TOKEN` set — files an issue against `github.com/Folken2/skills` (`status: "filed"`, `delivery: "github"`, `issue_url`).
2. `NUVEL_FEEDBACK_URL` set — POSTs the proposal as JSON to that relay (`status: "relayed"`, `delivery: "relay"`, plus the relay's `issue_url`/`id` if returned). If the relay fails, it falls through to the queue with a `relay_error`.
3. Otherwise — appends one JSON line to the feedback store's inbound queue, `<feedback dir>/feedback-queue.jsonl`, and returns `status: "queued"`, `delivery: "queue"`, the `queue_path` and the `feedback_dir`. If even that write fails the call returns an error, never a success.

**The feedback store.** All local skill feedback lands in one directory: `--feedback-dir`, else `$NUVEL_FEEDBACK_DIR`, else `~/.nuvel/feedback` (kept out of the skills hub so a read-only clone works). It holds `<skill>/<id>.json` per `record_feedback` report plus `feedback-queue.jsonl` for queued proposals. `check_skill_health` and `get_skill`'s `health` read both, so a queued proposal is visible immediately (repeat submissions of the same proposal share a `feedback_id` and count once). `theme/name` and `name` address the same skill. The former `NUVEL_FEEDBACK_QUEUE` variable is no longer read; the server logs a warning if it is set.

The code lives in `packages/nuvel-mcp/src/nuvel_mcp/` (`server.py` = JSON-RPC protocol and tools, shared by both transports via `SkillsMCPServer.dispatch`; `http_transport.py` = the HTTP transports, sessions and OAuth routes; `oauth.py` = the OAuth 2.1 store, authorization server and token validation; `skills_loader.py` = hub discovery/loading; `installer.py` = the plan/apply installer behind `install_skill`; `feedback.py` = the feedback store and health computation; `cli.py` = the `serve` / `client` / `token` commands shared by `nuvel-mcp` and `nuvel mcp`) with `nuvel/commands/mcp_serve.py` delegating to it. It is deliberately dependency-free so it runs without the ADK/agent stack installed; keep it that way.

## Role scoping

If you are deployed for a specific role, scope your skills by setting a theme.

### Via AGENTS.md

Add this at the top of this file:
```yaml
role: hr
```

When `role` is set, only load skills whose `theme` matches the value. The full list of themes is in `skills/index.json`.

### Via MCP server

```bash
nuvel mcp serve --theme hr
```

This filters `resources/list` and skill search tools to only the HR theme. A sales agent can run `--theme sales`, a software engineer `--theme software-development`.

## Generated Agent Shape

An ADK generated agent usually looks like:

```text
generated-agents/my-agent/
+-- my_agent/
|   +-- agent.py
|   +-- prompt/
|   +-- tools/
|   +-- skills/
|   +-- contexts/
|   +-- plugins/
|   +-- config/
+-- run_adk.py
+-- .env.example
```

Generated agents should remain runnable with:

```bash
pip install -r requirements.txt
DEV_MODE=true python run_adk.py
```

## Conventions

- Python 3.11+ is required.
- The PyPI package is `nuvel-cli`, but the import and CLI command remain `nuvel`.
- The console entry point is `nuvel = "nuvel.cli:main"` in `pyproject.toml`.
- File operations exposed to the meta-agent under `nuvel/tools/` are sandboxed to `AGENTS_OUTPUT_DIR`; preserve path traversal protections.
- Commit messages use conventional prefixes such as `feat:`, `fix:`, and `docs:`.
- Keep edits scoped. Avoid broad refactors when changing scaffolding, templates, or skills.

## Cost And Trace Data

`CostGuardPlugin` reads `nuvel/plugins/pricing.json` for the meta-agent, while generated agents read their copied `plugins/pricing.json`. Provider prefixes are stripped, so a model such as `openrouter/moonshotai/kimi-k2.5` can match a pricing key like `moonshotai/kimi-k2.5`.

Traces are written under `traces/` as per-event `.jsonl` files and consolidated per-conversation `.json` files used by `nuvel eval` and `nuvel dashboard`.

## Testing Guidance

Prefer targeted tests for narrow changes and broader scaffold tests for changes that affect generated project structure, CLI flags, backend behavior, or shared safety checks.

Path sandbox changes should include or update tests in `tests/test_path_guard.py`.
