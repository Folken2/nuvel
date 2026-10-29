# `nuvel-mcp` per-tester URL keys — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the `nuvel-mcp` HTTP transport run on a public address without GitHub OAuth, with each beta tester connecting through Claude's "No sign-in" custom connector by pasting one URL that carries a per-tester key.

**Architecture:** A `none` login mode in `OAuthConfig` drops the interactive OAuth routes. A new `url_keys` table in the existing `OAuthStore` maps the SHA-256 of each key to an operator-issued client. The request handler routes `/mcp/k/<key>` onto the existing Streamable HTTP handlers and authenticates with the key, yielding the same `TokenInfo` shape a `client_credentials` token does, so tenancy is unchanged.

**Tech Stack:** Python ≥3.11, stdlib only, pytest.

**Spec:** `docs/superpowers/specs/2026-10-29-mcp-url-keys-design.md`

## Global Constraints

- `nuvel-mcp` stays dependency-free.
- No credential or key literal in docs, tests' docstrings or specs; docs use `$ENV_VAR` placeholders. Tests generate keys at runtime.
- Default HTTP tool set and scopes unchanged.
- Commits: conventional prefixes, author Folken2 <folkenai21@gmail.com>, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. One commit per phase below.

## Phase 1 — spec and plan

- [ ] Commit the spec and this plan (`docs: spec and plan for nuvel-mcp URL keys`).

## Phase 2 — provider: login mode `none` and the `url_keys` table (`oauth.py`)

- [ ] `OAuthConfig.login: str | None` from `NUVEL_OAUTH_LOGIN`; `login_mode` returns `"none"` when it is `none`.
- [ ] `_TABLES` gains `url_keys`.
- [ ] `create_client(name, redirect_uris=None, *, url_key=False)` returns `(record, secret_or_key)`; with `url_key` the client is `grant_types: []`, `token_endpoint_auth_method: "none"`, `url_key: True`, and the key's hash goes in `url_keys`.
- [ ] `validate_url_key(key) -> TokenInfo | None` (constant-time `_find`, unrevoked, client exists, full scopes, `expires_at=inf`).
- [ ] `revoke`: a client id also revokes its key; a key, or a URL containing `/k/<key>`, revokes that key. `delete_client` drops its key rows. `list_clients` counts nothing new but keeps `url_key` in the view.
- [ ] `authorization_server_metadata(origin, login_mode=None)` drops the interactive endpoints for `none`.

## Phase 3 — transport: `/mcp/k/<key>` and redaction (`http_transport.py`)

- [ ] `_route` recognises `/mcp/k/<key>` (one segment, non-empty), stores the key on the handler for this request only, and routes to the `/mcp` handler for the verb. `/sse/k/…` and `/messages/k/…` fall through to `404`.
- [ ] `_authenticate` on a key path: `401` (no `resource_metadata`) for any `Authorization` header, a wrong, revoked or orphaned key; `_still_valid` re-checks the key.
- [ ] `_oauth_routes` omits `/register`, `/authorize`, `/oauth/callback`, `/oauth/dev-login` in `none` mode.
- [ ] `_redact()` used by `log_message` and the internal-error log: queries, `/k/<segment>` and `nvuk_…` runs.
- [ ] Startup log line says when URL keys are the only way in.

## Phase 4 — CLI (`cli.py`)

- [ ] `_oauth_provider_for_http`: refuse an unknown `NUVEL_OAUTH_LOGIN`; accept `none` on any bind (still needs `NUVEL_MCP_PUBLIC_URL` off loopback); warn when GitHub credentials or the dev login are set and ignored; skip the allowed-users warning in `none` mode.
- [ ] `client create --url-key` prints `client_id`, `client_name`, `url_key`, `connector_url` once; refuses `--redirect-uri` with it.
- [ ] `client list` shows type `url-key`; `token revoke` prints the key count; `--allow-remote` help text mentions key mode.

## Phase 5 — tests (`packages/nuvel-mcp/tests/test_mcp_url_keys.py`)

- [ ] The cases listed in the spec's Testing section; run `python -m pytest packages/nuvel-mcp/tests -q`.

(Phases 2–5 land as two commits: the feature with its tests, written test-first, then docs.)

## Phase 6 — docs and smoke test

- [ ] `docs/how-to/deploy-skills-mcp.md`: intro, settings table, a "Beta testers: URL keys" section, Railway key-mode steps, request-limit rows.
- [ ] Smoke test: `NUVEL_OAUTH_LOGIN=none NUVEL_MCP_PUBLIC_URL=http://127.0.0.1:<port> nuvel-mcp serve --transport http --allow-remote --host 0.0.0.0 --port <port>` with a temp store; `/healthz` 200, `initialize` + `tools/list` on the key URL, wrong and revoked key 401, `grep` the captured log for the key finds nothing.
- [ ] Open a ready-for-review PR against `main`.
