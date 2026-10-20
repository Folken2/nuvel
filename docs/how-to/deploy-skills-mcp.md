# Deploy the Skills MCP server over HTTP

`nuvel-mcp` speaks stdio by default, which is right for an MCP client on your own
machine. To collect [skill run traces](../reference/skill-run-traces.md) from
agents running elsewhere, serve it over **Streamable HTTP** behind an
authenticator.

## Run it

The HTTP transport needs the optional `http` extra (it pulls in the official
`mcp` SDK, version 1.30 or newer; the stdio server stays dependency-free):

```bash
export NUVEL_MCP_TOKENS="acme:$ACME_MCP_TOKEN,globex:$GLOBEX_MCP_TOKEN"
uvx --from "nuvel-mcp[http]" nuvel-mcp serve --transport http --auth bearer
```

The endpoint is `http://127.0.0.1:8765/mcp` (change it with `--host` / `--port`).
Without the extra, `--transport http` exits with
`pip install "nuvel-mcp[http]"`.

`--auth bearer` reads `$NUVEL_MCP_TOKENS` as `name:token,name:token`. A client
sends `Authorization: Bearer <token>`; the matching **name** becomes the caller's
principal and is stamped as the `tenant` of every trace it records. The token
itself is never stored or logged. Generate tokens with something like
`openssl rand -hex 32` and keep them in your secret manager, not in shell history.

Point an MCP client at it, for example Claude Code:

```bash
claude mcp add --transport http nuvel-skills https://mcp.example.com/mcp \
  --header "Authorization: Bearer $ACME_MCP_TOKEN"
```

## Put it behind TLS

Bearer tokens must never cross a network in clear text. `nuvel-mcp` does not
terminate TLS itself; run it behind something that does:

- **Reverse proxy on the same host** — keep the default `--host 127.0.0.1` and
  let Caddy, nginx or similar terminate TLS and forward to `127.0.0.1:8765`.
  The proxy forwards the public `Host` (`mcp.example.com`), which a loopback bind
  rejects with `421` unless you allow it with `--allowed-host`:

  ```bash
  export NUVEL_MCP_TOKENS="acme:$ACME_MCP_TOKEN"   # name:token pairs, from your secret store
  nuvel-mcp serve --transport http --auth bearer --allowed-host mcp.example.com
  ```

  `--allowed-host` requires `--auth`: a proxied public name is reachable from
  outside the machine, exactly like a non-loopback `--host`.

  Caddy (passes the client's `Host` upstream by default; run `nuvel-mcp` with
  the command above):

  ```text
  mcp.example.com {
      reverse_proxy 127.0.0.1:8765
  }
  ```

  nginx (same `nuvel-mcp` command):

  ```nginx
  location /mcp {
      proxy_pass http://127.0.0.1:8765;
      proxy_set_header Host $host;
  }
  ```

  Alternatively, have the proxy rewrite `Host` to the upstream address (Caddy:
  `header_up Host {upstream_hostport}`; nginx: `proxy_set_header Host 127.0.0.1:8765;`),
  in which case no `--allowed-host` is needed. Keep `--auth bearer` anyway: the
  proxy makes the server public whichever way `Host` arrives.

- **Container platform** (Railway, Fly, Cloud Run, Kubernetes) — bind
  `--host 0.0.0.0 --port $PORT` and expose the service only through the
  platform's HTTPS endpoint.

Binding anything other than `127.0.0.1`, `localhost` or `::1` without `--auth`
is refused before the server starts. Without `--auth`, every process on the
machine can call the server, so use that only for local testing.

On a loopback bind the server also checks `Host` and `Origin`, with or without
`--auth`. Without these checks any website the user visits could reach the server
through DNS rebinding: the browser treats the rebound page as same-origin. A
`Host` other than `127.0.0.1`, `localhost`, `[::1]` or a `--allowed-host` name
(any port, case-insensitive) gets `421`, and an `Origin` that is present and not
`http://` or `https://` one of those names gets `403`. `--allowed-host` takes a
bare host name (no scheme, port or path), is repeatable, requires `--auth`, and
the checks stay on when `--auth` is set. On a public bind
(`--host 0.0.0.0`) `Host` is not checked; `--auth` is required there instead.

## What is exposed over HTTP, and why

By default HTTP exposes exactly two tools:

| Tool | Why it is safe to expose |
|---|---|
| `record_run_trace` | Takes a closed-schema `nuvel.skill_run/1` trace with no free-text fields; anything else is rejected without being echoed. |
| `check_skill_health` | Read-only. Over HTTP `plugin` is required, and the result is only `{"skill", "plugin", "runs"}`: the run-trace summary for the caller's tenant. Nothing from the local feedback store (outcome counts, queued proposals and their free text) is returned. |

Everything else is off unless you name it in `--tools`:

- `record_feedback` and `propose_improvement` accept **free text**, which can
  carry user content a hosted endpoint should never receive.
  `propose_improvement` can also file GitHub issues with the server's
  `GITHUB_TOKEN` on behalf of any caller.
- The hub tools (`search_skills`, `get_skill`, `install_skill`, …) serve a
  local skills hub, and `install_skill` writes to the server's filesystem.

stdio keeps the full tool set: it runs on the user's own machine, where free text
never leaves.

## Request limits

Every request is checked before the MCP SDK sees it:

| Condition | Response |
|---|---|
| `Host` not loopback or `--allowed-host` (loopback bind only) | `421` |
| `Origin` present and not loopback or `--allowed-host` (loopback bind only) | `403` |
| Missing or wrong token, or more than one `Authorization` header | `401` |
| Body over 65536 bytes (by `Content-Length`, or counted while streaming a chunked body) | `413` |
| Body that is not valid JSON | `400` |

None of these responses echo the request body. Sessions are stateless and
responses are plain JSON, so every request is authenticated on its own.

## Bring your own authenticator

`--auth module:factory` imports `module` and calls `factory()` with no arguments.
It must return an object with
`authenticate(headers: Mapping[str, str]) -> str | None` (see
`nuvel_mcp.auth.Authenticator`): return the principal name to accept the
request, `None` to reject it with `401`. Header names are matched
case-insensitively. A factory that returns anything else, including `None`, is
refused at startup (exit 1): it never turns authentication off. The `--auth` value is only ever read from the command line.

## What this package does not do

Managed hosting, an OAuth provider, the tenant model and hosted trace storage live
in the private nuvel-cloud service, which plugs in through `--auth` and
`--trace-store`. This package only makes the open-source server deployable.
