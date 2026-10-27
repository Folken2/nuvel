# Deploy the Skills MCP server over HTTP

`nuvel-mcp` speaks stdio by default, which is right for an MCP client on your own
machine. To serve a skills hub, or to collect
[skill run traces](../reference/skill-run-traces.md) from agents running
elsewhere, serve it over HTTP. The transport is pure standard library: there is
nothing extra to install, and `nuvel-mcp` has no dependencies and no extras.

It speaks Streamable HTTP on `/mcp`, legacy HTTP+SSE on `/sse` and `/messages`,
and answers `GET /healthz`. It is its own OAuth 2.1 authorization server (PKCE,
dynamic client registration, refresh-token rotation, GitHub as the login
provider), so every MCP request needs a bearer access token.

## Run it locally

```bash
nuvel-mcp serve --transport http --skills-dir ./skills
```

This binds `127.0.0.1:8080` (change with `--host` / `--port`) and needs a login
provider. For local tests only, `NUVEL_OAUTH_DEV_LOGIN=1` enables a built-in dev
login that is refused on any non-loopback bind. To skip OAuth entirely on your
own machine:

```bash
nuvel-mcp serve --transport http --no-auth --skills-dir ./skills
```

`--no-auth` is **loopback-only** (refused on any other `--host`) and prints a
warning: every method and tool, writes included, is open to anything that can
reach the port. With `--no-auth` there is no caller identity, so run traces are
stored without a `tenant`, the same as stdio.

`--skills-dir` is optional. Without it the server runs feedback-only: just the
trace and health tools.

## Deploy it for other people

Binding a non-loopback address takes a deliberate `--allow-remote`, plus a public
URL and a GitHub OAuth app:

```bash
export NUVEL_MCP_PUBLIC_URL="https://mcp.example.com"
export NUVEL_OAUTH_GITHUB_CLIENT_ID="$GITHUB_OAUTH_CLIENT_ID"
export NUVEL_OAUTH_GITHUB_CLIENT_SECRET="$GITHUB_OAUTH_CLIENT_SECRET"
export NUVEL_OAUTH_ALLOWED_USERS="alice,bob"
nuvel-mcp serve --transport http --allow-remote --host 0.0.0.0 --skills-dir ./skills
```

| Setting | What it does |
|---|---|
| `--allow-remote` | Required to bind a non-loopback `--host`. |
| `NUVEL_MCP_PUBLIC_URL` | The origin clients reach the server at (for example `https://mcp.example.com`). It is the OAuth issuer and the audience of every token. Required on a non-loopback bind. |
| `NUVEL_OAUTH_GITHUB_CLIENT_ID` / `NUVEL_OAUTH_GITHUB_CLIENT_SECRET` | A GitHub OAuth app whose callback URL is `<NUVEL_MCP_PUBLIC_URL>/oauth/callback`. This is how people log in when they connect an interactive client. |
| `NUVEL_OAUTH_ALLOWED_USERS` | Comma-separated GitHub logins allowed to log in. Unset, **any** GitHub user may, and the server warns at startup. Set it. |
| `NUVEL_OAUTH_REDIRECT_ALLOWLIST` | Extra redirect URIs accepted at client registration, beyond `https://` and loopback ones. |
| `NUVEL_MCP_OAUTH_STORE` | The token and client store (default `~/.nuvel/oauth.json`, mode 0600). Tokens and secrets are kept only as hashes. Put it on a persistent volume. |
| `NUVEL_MCP_ALLOWED_ORIGINS`, `NUVEL_MCP_MAX_BODY_BYTES`, `NUVEL_MCP_MAX_SESSIONS`, `NUVEL_MCP_SESSION_TTL` | Optional tuning of the origin allow-list and the request and session caps. |
| `NUVEL_MCP_REQUEST_DEADLINE` | Wall-clock seconds a client gets to send one whole request (request line, headers, body), however slowly it trickles. Default 30. |
| `NUVEL_MCP_READ_TIMEOUT` | Seconds a single socket read or write may stall, and how long an idle keep-alive connection is kept. Default 30. The idle keep-alive limit is the smaller of this and `NUVEL_MCP_REQUEST_DEADLINE`. |
| `NUVEL_MCP_MAX_CONNECTIONS` | Connections (handler threads, open SSE streams included) served at once. Default 128. Slots are taken before authentication, and long-lived GET streams hold them, so a public deployment should sit behind a buffering reverse proxy with per-client connection limits. |

The two timeouts must be greater than 0 and `NUVEL_MCP_MAX_CONNECTIONS` at least 1 (0 does not disable them); any other value stops the server at startup with exit code 2.

Keep the GitHub secret and every other credential in your secret manager and
inject it as an environment variable; never put one on a command line or in a
file you commit.

### Clients for agents and services

Interactive clients register themselves dynamically and log in through GitHub.
For an agent or service that has no browser, issue a client from the server's
host. It uses the same store, so the running server sees it at once:

```bash
nuvel-mcp client create --name acme-agent        # prints client_id and client_secret once
nuvel-mcp client list                            # no secrets
nuvel-mcp client delete "$CLIENT_ID"             # also revokes its tokens
nuvel-mcp token revoke "$TOKEN_OR_CLIENT_ID"     # one token, or all of a client's
```

Add `--redirect-uri` to `client create` for a client that also uses the
authorization-code flow. The `client_secret` is shown only at creation. A service
exchanges its `client_id` and `client_secret` for an access token with the
`client_credentials` grant at `POST <NUVEL_MCP_PUBLIC_URL>/token` (HTTP Basic
authentication), then sends `Authorization: Bearer <access_token>` to `/mcp`.

Tokens carry the scopes `skills:read` (every MCP method and the read tools) and
`skills:write` (the tools that write). Point an MCP client at the server, for
example Claude Code, which runs the OAuth flow itself:

```bash
claude mcp add --transport http nuvel-skills https://mcp.example.com/mcp
```

## Behind a reverse proxy

`nuvel-mcp` does not terminate TLS. Run it behind something that does, and tell
it its public address with `NUVEL_MCP_PUBLIC_URL`; without that setting it would
derive the OAuth origin from the request's `Host` header.

```bash
export NUVEL_MCP_PUBLIC_URL="https://mcp.example.com"
nuvel-mcp serve --transport http --skills-dir ./skills        # still on 127.0.0.1:8080
```

Caddy:

```text
mcp.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

nginx (SSE needs buffering off):

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}
```

Because the proxy and the server share a machine, the server can stay on
loopback and you do not need `--allow-remote`. On a container platform (Railway,
Fly, Cloud Run, Kubernetes) bind `--allow-remote --host 0.0.0.0 --port $PORT`
instead, set `NUVEL_MCP_PUBLIC_URL` to the platform's HTTPS URL, and expose the
service only through it.

A browser request whose `Origin` is not loopback, your public origin or one in
`NUVEL_MCP_ALLOWED_ORIGINS` is refused with `403`, with or without `--no-auth`.
That stops a web page the user visits from reaching a loopback server through DNS
rebinding.

## What is exposed over HTTP, and why

OAuth decides who may call the server; the defaults below decide what a caller can
do. They apply to every HTTP server, whether or not you use `--no-auth`.

**Default tool set.** Without `--tools`, HTTP exposes:

| Tool | Why it is exposed |
|---|---|
| `search_skills`, `list_skills`, `list_themes`, `get_skill`, `fence_value` | The hub read tools: serving the hub is what a remote server is for. |
| `record_run_trace` | Takes a closed-schema `nuvel.skill_run/1` trace with no free-text fields; anything else is rejected without being echoed. |
| `check_skill_health` | Read-only; see remote mode below. |
| `record_recipe_signal` | Takes a closed-schema `nuvel.recipe_signal/1` event with identifiers and counters only, no free text. See [Skill run traces](../reference/skill-run-traces.md#recipe-signals-and-shared-recipes). |
| `share_recipe` | Takes a `nuvel.recipe/2` recipe: closed structure (actions, tool kinds, placeholder slots) plus free text that the server masks with a per-language allowlist before anything is stored, relayed or logged. Spanish and English are masked; other languages are shared as structure only. A format problem is rejected with field paths only. See [Skill run traces](../reference/skill-run-traces.md#shared-recipes-nuvelrecipe2). |

Off unless you name them in `--tools`:

- `record_feedback` and `propose_improvement` accept **free text**, which can
  carry user content a hosted endpoint should never receive.
  `propose_improvement` can also file GitHub issues with the server's
  `GITHUB_TOKEN` on behalf of any caller.
- `install_skill` writes to the server's filesystem.

stdio keeps the full tool set: it runs on the user's own machine, where free text
never leaves.

**Remote mode.** Every HTTP request runs in remote mode: no tool returns data
derived from the local feedback store (outcome counts, queued proposals and their
free text). `get_skill` leaves out its `health` report, and `check_skill_health`
requires `plugin` and returns only `{"skill", "plugin", "runs"}`: the run-trace
summary for the caller's own tenant.

**Tenant is the OAuth `client_id`.** For each authenticated request, the token's
`client_id` is the caller's tenant: it is stamped on every trace it records and
scopes what `check_skill_health` returns, so two clients never see each other's
runs. It is never the `subject` (a person's GitHub login). Use one client per
tenant, which is what `client create` is for.

## Request limits

| Condition | Response |
|---|---|
| Missing, invalid or expired access token, or more than one `Authorization` header | `401` |
| Valid token without the scope the method needs | `403` |
| `Origin` not allowed | `403` |
| Body over the cap (`NUVEL_MCP_MAX_BODY_BYTES`, default 1 MiB) | `413` |
| Body sent with `Transfer-Encoding: chunked` | `411` |
| Request not fully received within `NUVEL_MCP_REQUEST_DEADLINE` seconds | `408` if it stalled in the body; a slow request line or headers just closes the connection |
| A second `GET /mcp` stream on a session that already has one open | `409`; the open stream keeps working |
| More than `NUVEL_MCP_MAX_CONNECTIONS` connections open | `503` with `Retry-After: 1`, sent without starting a handler thread |
| Body that is not valid JSON | `400` |

Error responses do not echo the request body, and a failing trace store reports a
generic error to the caller; the detail goes to the server log only.

A request with `Expect: 100-continue` gets the interim `100 Continue` only once
it has passed the framing checks, `Origin` and authentication and its body is
about to be read; a request refused by any of those gets its one final status
and no `100`.
