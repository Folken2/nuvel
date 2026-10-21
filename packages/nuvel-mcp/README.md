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

The HTTP transport (Streamable HTTP on `/mcp`, legacy SSE, OAuth 2.1) is part of
the same dependency-free package; there are no extras. It binds
`127.0.0.1:8080` and requires an OAuth token unless you pass `--no-auth` on a
loopback bind. Create a client for a trace-recording workload and serve:

```bash
uvx nuvel-mcp client create --name acme-radar
uvx nuvel-mcp serve --transport http
```

Over HTTP the default tools are the hub read tools plus `record_run_trace` and
`check_skill_health`; `record_feedback`, `propose_improvement` and
`install_skill` are exposed only when named in `--tools`. The tenant of each
run trace is the OAuth `client_id`.

See the [deployment guide](https://folken2.github.io/nuvel/how-to/deploy-skills-mcp/)
for TLS and what is exposed over HTTP.

## Relationship to `nuvel-cli`

This package is released from the same monorepo as
[`nuvel-cli`](https://pypi.org/project/nuvel-cli/) at the same version.
`nuvel mcp serve` delegates to `nuvel-mcp`, and the old `nuvel.mcp` import path
is a deprecated shim; import `nuvel_mcp` instead.
