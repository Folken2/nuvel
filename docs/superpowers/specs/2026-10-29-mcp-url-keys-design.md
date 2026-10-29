# `nuvel-mcp` per-tester URL keys — design

**Date:** 2026-10-29
**Status:** approved (2026-10-29, scope set by the deployment session)
**Author:** Folken2

## Problem

`nuvel-mcp` is being deployed to Railway for beta testers. The HTTP transport
is gated by OAuth 2.1 and, on a non-loopback bind, refuses to start unless a
GitHub OAuth app is configured (`_oauth_provider_for_http` in `cli.py`). The
user declined GitHub OAuth for the beta: testers should connect with no sign-in
step at all.

Claude's custom connectors offer a **No sign-in** mode, where the only thing the
tester supplies is the server URL. Fixed request headers are a beta feature
limited to some organizations, so a credential cannot travel in an
`Authorization` header. It has to travel in the URL.

A fully open server was considered and rejected: it gives no per-tester tenant
(every trace would land in one tenant), no way to cut off one tester, and lets
anyone who finds the address fill the `/data` volume. Per-tester keys keep the
same paste-a-URL experience and fix all three.

## Decisions

### 1. A login mode with no interactive login: `NUVEL_OAUTH_LOGIN=none`

A new variable, `NUVEL_OAUTH_LOGIN`. Its only accepted value is `none`; unset
keeps today's behaviour (GitHub when its credentials are set, else the dev
login, else refuse to start). Any other value stops the server at startup with
exit code 2.

An explicit switch, rather than "no GitHub credentials means key mode", so that
a deploy that merely *lost* its GitHub variables still fails closed instead of
silently becoming key-only.

With `NUVEL_OAUTH_LOGIN=none`:

- The interactive endpoints are not routed: `/authorize`, `/register` (dynamic
  client registration), `/oauth/callback` and `/oauth/dev-login` answer `404`.
- `/token` stays, so operator-issued `client_credentials` clients keep working.
- The authorization server metadata drops `authorization_endpoint` and
  `registration_endpoint` and advertises only the `client_credentials` grant.
- `NUVEL_MCP_PUBLIC_URL` is still required on a non-loopback bind (it is the
  token audience, and `client create` uses it to print the connector URL).
- GitHub credentials, if also set, are ignored with a startup warning, as is
  `NUVEL_OAUTH_DEV_LOGIN`. The `NUVEL_OAUTH_ALLOWED_USERS` warning is not
  printed: no user ever logs in.

URL keys are not tied to this mode: they work in every gated mode (GitHub and
dev too), because they are an operator-issued credential like a client secret.
`none` only turns off the interactive login.

### 2. The key travels as a path segment: `/mcp/k/<key>`

Two options: a path segment (`/mcp/k/<key>`) or a query parameter
(`/mcp?key=<key>`). Path segment, because:

- Claude's connector docs don't say whether a query string survives every
  request the connector makes; MCP clients routinely build URLs by joining paths
  onto the configured base, and some drop or rewrite queries. A path is part of
  the endpoint itself, so it is kept on every POST, GET and DELETE.
- The access log already redacts query strings wholesale; a path key needs an
  explicit redaction (below), which is easy to test.
- Proxies and CDNs treat both alike for logging, so neither is safer there; the
  operator's proxy must not log full URLs either way (documented).

The server routes `/mcp/k/<key>` to the same Streamable HTTP handlers as `/mcp`
(POST, GET stream, DELETE), authenticating with the key instead of a bearer
token. A request on a key path that *also* carries an `Authorization` header is
refused with `401`: one credential per request, as with duplicate
`Authorization` headers today.

A `401` on a key path carries `WWW-Authenticate: Bearer error="invalid_token"`
without `resource_metadata`, so a client is not sent off to an OAuth discovery
that cannot help it.

**Legacy `/sse` + `/messages` is not supported with keys.** Claude's custom
connectors speak Streamable HTTP, and the legacy transport would need the key in
two URLs (the `endpoint` event would have to embed it) for no tester who needs
it. `/sse/k/…` and `/messages/k/…` answer `404`. Bearer-token clients keep the
legacy transport unchanged.

### 3. Keys map to an issued client; the tenant is its `client_id`

`nuvel-mcp client create --name <tester> --url-key` creates a client and a key:

- The client has no secret and no grant types (`grant_types: []`,
  `token_endpoint_auth_method: "none"`, `url_key: true`), so it cannot use
  `/token` or `/authorize`. It exists to be the tenant.
- The key is `nvuk_` + 32 random bytes (urlsafe base64, so it is path-safe). Only
  its SHA-256 hash is stored, in a new `url_keys` table of the existing store
  (`NUVEL_MCP_OAUTH_STORE`): `{hash: {client_id, issued_at, revoked}}`.
- The command prints JSON with `client_id`, `client_name`, `url_key` and
  `connector_url` (`$NUVEL_MCP_PUBLIC_URL/mcp/k/<key>`, or the bare path with a
  note when the variable is unset) exactly once, and a reminder on stderr that
  only the hash is kept.
- `--url-key` and `--redirect-uri` are mutually exclusive.

A valid key authenticates as `TokenInfo(client_id, subject="client:<client_id>",
scope={skills:read, skills:write}, …)` — the same principal shape and scopes a
`client_credentials` token gets today — so tenancy, session ownership and
`check_skill_health` scoping need no change. The default HTTP tool set is
unchanged; scopes only gate what the tool set already exposes.

Keys don't expire; revocation is the control. Lookup is constant-time over the
table (`_find`), like every other token lookup. A key is valid only while it is
unrevoked *and* its client still exists.

### 4. Revocation

- `nuvel-mcp token revoke <client_id>` revokes the client's tokens **and** its
  URL key.
- `nuvel-mcp token revoke <key>` (or the full connector URL) revokes that key.
- `nuvel-mcp client delete <client_id>` deletes the client and its key entry.

The store is re-read when its file changes, so a running server refuses the key
on the next request. An open `GET /mcp/k/<key>` stream re-checks the key on every
ping and closes when it is revoked, as bearer streams do.

`client list` shows such clients with type `url-key`.

### 5. Keys never reach a log or a response

- The access log redacts `/k/<anything>` path segments (and any `nvuk_…` run,
  belt and braces) to `<redacted>`, alongside the existing query redaction.
- The internal-error log line redacts the path the same way.
- Error responses never echo the path. `404`, `401` and `403` bodies are fixed
  strings.
- The key is never placed in a `TokenInfo`, session or exception message.

## Testing

In `packages/nuvel-mcp/tests/test_mcp_url_keys.py`, against real servers on
`127.0.0.1:0`:

- a key from `create_client(..., url_key=True)` does `initialize` + `tools/list`
  on `/mcp/k/<key>`, with the session id round-trip, and the tenant is the
  client's `client_id`;
- a wrong key, a revoked key (by client id, by key, by URL) and a deleted
  client's key all get `401`; a key path plus an `Authorization` header gets
  `401`; `/sse/k/<key>` gets `404`;
- an open key stream closes after the key is revoked;
- the access log captured from stderr never contains the key;
- in `none` mode the interactive routes are `404`, `/token` still works for a
  `client_credentials` client, and the metadata advertises neither
  `authorization_endpoint` nor `registration_endpoint`;
- the CLI: `serve` with `NUVEL_OAUTH_LOGIN=none` starts on a non-loopback bind
  without GitHub credentials but still needs `NUVEL_MCP_PUBLIC_URL`; an unknown
  `NUVEL_OAUTH_LOGIN` value exits 2; `client create --url-key` prints the
  connector URL once and stores only the hash; `--url-key --redirect-uri` exits 2.

Plus a manual smoke test: `serve --allow-remote --host 0.0.0.0` with
`NUVEL_OAUTH_LOGIN=none` and no GitHub variables.

## Docs

- `docs/how-to/deploy-skills-mcp.md`: a "Beta testers: URL keys" section, the
  Railway steps for key mode, the request-limit rows for key paths, and
  `NUVEL_OAUTH_LOGIN` in its settings table, next to every other
  `NUVEL_OAUTH_*` / `NUVEL_MCP_*` variable.
- `docs/reference/env-vars.md` lists the variables a *scaffolded agent* reads
  and documents none of the `nuvel-mcp` server's, so it is left unchanged.

## Out of scope

Key expiry, rotating a key in place (issue a new client instead), per-key rate
limits, and keys for the legacy SSE transport.
