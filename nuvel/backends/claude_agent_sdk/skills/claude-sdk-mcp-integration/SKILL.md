---
name: claude-sdk-mcp-integration
description: Wiring external MCP servers into a Claude Agent SDK agent — stdio and HTTP transports, dict format, when to use SDK MCP vs external, naming conventions, and common pre-built servers (filesystem, fetch, sequential-thinking). Read when you need to give Claude access to a service via an existing MCP server, when choosing between writing your own tools and consuming an external one, or when debugging "tool not found" errors.
type: knowledge
---

# External MCP servers in the Claude Agent SDK

The SDK supports three kinds of "MCP server" entries in `mcp_servers`:

1. **In-process SDK servers** — the result of `create_sdk_mcp_server(...)`. These hold your `@tool` functions and run in the agent's Python process. Covered in `claude-sdk-tool-creation`.
2. **External stdio servers** — separate processes the SDK spawns and talks to over stdin/stdout. Most published MCP servers (Anthropic's, community) work this way.
3. **External HTTP servers** — remote MCP servers reached over HTTP/SSE. Less common; useful for hosted services.

This skill covers (2) and (3). Pick external servers when someone has already solved your integration; pick SDK servers when you're wrapping your own logic.

## stdio servers (the common case)

```python
options = ClaudeAgentOptions(
    mcp_servers={
        "fs":    {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]},
        "fetch": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]},
    },
    allowed_tools=[
        "mcp__fs__read_file",
        "mcp__fs__list_directory",
        "mcp__fetch__fetch",
    ],
)
```

The dict shape is fixed: `type`, `command`, `args`. Optional keys: `env` (dict of env vars), `cwd` (working dir for the subprocess).

The SDK starts the subprocess on first use, keeps it alive for the session, and tears it down on `__aexit__`. You don't manage the lifecycle.

## HTTP servers

```python
mcp_servers={
    "remote": {"type": "http", "url": "https://mcp.example.com/sse", "headers": {"Authorization": "Bearer ..."}},
}
```

Use when the MCP server is hosted (Composio, an internal service, etc.). The transport handles SSE under the hood.

## The naming convention is the same

External tools also become `mcp__<server-key>__<tool>`. The server key is the dict key you chose in `mcp_servers`, **not** the server's internal name. So if you wire `mcp_servers={"fs": ...}` and the server exports a `read_file` tool, Claude calls `mcp__fs__read_file`.

Pre-approve external tools the same way you do SDK tools:

```python
allowed_tools=["mcp__fs__read_file", "mcp__fs__write_file"]
```

You don't have to enumerate every tool the server exports — only the ones you want pre-approved. Tools you leave out will surface a permission prompt (or be blocked entirely under stricter permission modes).

## Pre-built servers worth knowing

| Server | Install | What it gives Claude |
|--------|---------|---------------------|
| `@modelcontextprotocol/server-filesystem` | `npx -y` | Sandboxed read/write/list within a directory. Use over Claude's built-in `Read`/`Write` when you want explicit path scoping. |
| `mcp-server-fetch` | `uvx` | HTTP fetch with sane defaults — better than letting Claude shell out to curl. |
| `@modelcontextprotocol/server-git` | `npx -y` | Git operations. |
| `@modelcontextprotocol/server-sequential-thinking` | `npx -y` | Structured chain-of-thought scratchpad. Worth it for complex tasks. |
| Composio Tool Router | hosted (HTTP) | ~1000 toolkits behind one MCP. See `references/composio-integration.md`. |

Don't wire all of these by default — every additional MCP server adds tools to Claude's context. Pick what your agent actually needs.

## When to write an SDK tool vs use an external server

**Write your own (SDK MCP)** when:
- The logic is specific to your domain (CRM lookup, your DB schema, your auth).
- You need to share Python state with the rest of the agent process.
- Performance matters and stdio overhead is real.

**Use an external server** when:
- Someone has already built it well (filesystem, fetch, git).
- You want process isolation (untrusted code, resource limits).
- The server is maintained separately from your agent code.

The two compose freely — most production agents have one SDK MCP server (custom domain tools) plus 1-3 external servers (filesystem, fetch, maybe Composio).

## Debugging "tool not found"

When Claude reports it can't call a tool, in order of likelihood:

1. **You used the unqualified name.** `allowed_tools=["read_file"]` does nothing for an MCP tool. Use `mcp__fs__read_file`.
2. **Server failed to start.** `claude-agent-sdk` logs subprocess startup; check stderr in dev. Common causes: `npx` not on PATH, wrong package name, missing env var the server needs.
3. **`disallowed_tools` blocks it.** Higher precedence than `allowed_tools`.
4. **`permission_mode="dontAsk"` plus tool not in `allowed_tools`.** That mode silently denies anything not pre-approved.
5. **Tool name typo in the server itself.** Run the server standalone (`npx ...`) and inspect its `tools/list` response to see actual names.

## Quick reference

| Task | API |
|------|-----|
| Wire an external stdio server | `mcp_servers={"key": {"type": "stdio", "command": ..., "args": [...]}}` |
| Wire an HTTP server | `mcp_servers={"key": {"type": "http", "url": "...", "headers": {...}}}` |
| Pre-approve external tool | `allowed_tools=["mcp__<key>__<tool>"]` |
| Pass env to subprocess | `{"type": "stdio", ..., "env": {"VAR": "..."}}` |
| Mix SDK + external | `mcp_servers={"core": sdk_server, "fs": {...stdio...}}` |
