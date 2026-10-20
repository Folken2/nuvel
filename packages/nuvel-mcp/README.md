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

# HTTP transport needs the optional `mcp` SDK
pip install "nuvel-mcp[http]"
```

Run `nuvel-mcp serve --help` for all flags (`--theme`, `--require`,
`--with-org-memory`, `--feedback-dir`).

## Relationship to `nuvel-cli`

This package is released from the same monorepo as
[`nuvel-cli`](https://pypi.org/project/nuvel-cli/) at the same version.
`nuvel mcp serve` delegates to `nuvel-mcp`, and the old `nuvel.mcp` import path
is a deprecated shim; import `nuvel_mcp` instead.
