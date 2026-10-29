# nuvel-mcp

A dependency-free [MCP](https://modelcontextprotocol.io) server that exposes a
Nuvel skills hub (a directory of `SKILL.md` skills) to any MCP client. The
default stdio transport needs nothing beyond the standard library.

```bash
# one-shot, no install
uvx nuvel-mcp serve --skills-dir /path/to/skills-hub

# or install it
pip install nuvel-mcp
nuvel-mcp serve --skills-dir /path/to/skills-hub
```

Run `nuvel-mcp serve --help` for all flags (`--theme`, `--require`,
`--with-org-memory`, `--feedback-dir`, `--tools`, `--trace-store`,
`--transport`, `--host`, `--port`, `--allow-remote`, `--no-auth`,
`--require-auth`). Without `--skills-dir` the server starts in feedback-only
mode.

State lives in files by default. Set `NUVEL_MCP_DATABASE_URL` (for example a
Neon pooled connection string) and OAuth, run traces, recipes and a per-request
usage log all go to Postgres instead; that needs `pip install
"psycopg[binary,pool]"`, and the package itself stays dependency-free. The
schema is migrated at startup; `nuvel-mcp db migrate|status|import` manage it,
and `db import` copies existing files in. See the
[deploy guide](https://github.com/Folken2/nuvel/blob/main/docs/how-to/deploy-skills-mcp.md#store-everything-in-postgres-neon).

The HTTP transport (Streamable HTTP on `/mcp`, legacy SSE, OAuth 2.1) is part of
the same dependency-free package; there are no extras. It binds
`127.0.0.1:8080` and requires an OAuth token unless you pass `--no-auth` on a
loopback bind. Create a client for a trace-recording workload and serve:

```bash
uvx nuvel-mcp client create --name acme-radar
uvx nuvel-mcp serve --transport http
```

`client create` prints a `client_id` and a `client_secret` (shown once). A
workload exchanges them for an access token with the `client_credentials` grant,
then sends it as a bearer token to `/mcp`. On the default loopback bind the
server is `http://127.0.0.1:8080`; for a remote deployment use its public URL,
`$NUVEL_MCP_PUBLIC_URL`, instead:

```bash
curl -s -u "$NUVEL_CLIENT_ID:$NUVEL_CLIENT_SECRET" \
  -d grant_type=client_credentials "http://127.0.0.1:8080/token"
# -> {"access_token": "...", ...}; call /mcp with
#    Authorization: Bearer $ACCESS_TOKEN
```

For an MCP client that can only be given a URL (a Claude custom connector with
no sign-in), `uvx nuvel-mcp client create --name alice --url-key` prints a
per-client `connector_url` (`$NUVEL_MCP_PUBLIC_URL/mcp/k/<key>`) once. Set
`NUVEL_OAUTH_LOGIN=none` to run a public server with no interactive login.

The [deployment guide](https://folken2.github.io/nuvel/how-to/deploy-skills-mcp/)
covers scopes and the interactive flow.

Over HTTP the default tools are the hub read tools plus `record_run_trace`,
`check_skill_health`, `record_recipe_signal` and `share_recipe`; `record_feedback`, `propose_improvement` and
`install_skill` are exposed only when named in `--tools`. The tenant of each
run trace is the OAuth `client_id`.

See the [deployment guide](https://folken2.github.io/nuvel/how-to/deploy-skills-mcp/)
for TLS and what is exposed over HTTP.

## Relationship to `nuvel-cli`

This package is released from the same monorepo as
[`nuvel-cli`](https://pypi.org/project/nuvel-cli/) at the same version.
`nuvel mcp serve` delegates to `nuvel-mcp`, and the old `nuvel.mcp` import path
is a deprecated shim; import `nuvel_mcp` instead.
