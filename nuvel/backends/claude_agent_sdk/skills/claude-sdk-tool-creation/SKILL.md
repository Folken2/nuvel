---
name: claude-sdk-tool-creation
description: Building custom tools for Claude Agent SDK agents — the @tool decorator, schema dicts, return shape, error returns, and the mcp__server__tool naming gotcha. Read when adding a new tool to a Claude Agent SDK project, when you need to expose Python functions to Claude, or when designing tool boundaries.
---

# Building tools for the Claude Agent SDK

Tools in this SDK are not standalone functions — they're entries in an in-process MCP server. You define them with `@tool`, register them with `create_sdk_mcp_server`, and Claude calls them through that server like any other MCP-exposed tool. This is the only "private" mechanism worth knowing; once you internalize it, every other tool concept (permissions, naming, error handling) follows.

## The minimal example

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("greet", "Greet a user by name", {"name": str})
async def greet(args):
    return {"content": [{"type": "text", "text": f"Hello, {args['name']}!"}]}

server = create_sdk_mcp_server(name="my-tools", version="1.0.0", tools=[greet])
```

Three things happen here. The decorator wraps the async function with metadata (`name`, `description`, `input_schema`). `create_sdk_mcp_server` bundles tools into one MCP server. The server is later passed to `ClaudeAgentOptions(mcp_servers={"my-tools": server})`.

## The naming gotcha

After registration, Claude does **not** see a tool named `greet`. It sees `mcp__my-tools__greet` — `mcp__<server-name>__<tool-name>`. This matters in two places:

1. **`allowed_tools` pre-approval**: to skip permission prompts you must list the qualified name: `allowed_tools=["mcp__my-tools__greet"]`. Listing `"greet"` does nothing.
2. **Hook matchers**: a `PreToolUse` matcher targeting your tool needs the qualified name (or a regex matching it).

The most common bug we see is `allowed_tools=["greet"]` followed by surprise that the tool prompts on every call. Always use the qualified name.

## Return shape

Tools return a dict shaped like an MCP tool result:

```python
return {
    "content": [
        {"type": "text", "text": "human-readable result"},
    ],
}
```

For errors, set `is_error=True` at the top level and put the message in a text content block:

```python
return {
    "content": [{"type": "text", "text": "Division by zero"}],
    "is_error": True,
}
```

Don't raise — Claude can't see the exception. Always return a result. If you raise, the SDK surfaces it as a generic protocol error and Claude is left without context to recover.

## Schema is positional, not Pydantic

The third argument to `@tool` is a dict mapping parameter names to Python types:

```python
@tool("calc", "Calculate a + b", {"a": float, "b": float})
```

Supported types: `str`, `int`, `float`, `bool`, `list`, `dict`. For richer schemas (enums, nested objects, descriptions on each field), pass a JSON Schema dict instead:

```python
@tool("send_email", "Send an email", {
    "type": "object",
    "properties": {
        "to":      {"type": "string", "format": "email"},
        "subject": {"type": "string"},
        "urgent":  {"type": "boolean", "default": False},
    },
    "required": ["to", "subject"],
})
```

Use the dict shorthand for prototyping; switch to JSON Schema once you need defaults, descriptions, or constraints — Claude reads them and writes better tool calls.

## When to split into multiple servers

Default: one server per agent. A handful of tools in one `tools/` package, one `create_sdk_mcp_server` call.

Split when:
- Tools have distinct **lifecycle** — e.g. one set needs DB connections at server start, another doesn't.
- You want to **disable** half the tools via env config without touching code (`mcp_servers={"core": core, **({"admin": admin} if is_admin else {})}`).
- A subagent should only see a subset of tools (each subagent gets its own `ClaudeAgentOptions` and you pick which servers to wire).

Don't split for organizational reasons alone — Claude doesn't care, and you pay an extra `mcp__server__` prefix in every name.

## Common patterns

For deeper patterns — long-running tools, streaming results, sharing state between tools, parameter validation, mocking in tests — see `references/tool-patterns.md`.

## Quick reference

| Concept | API |
|---------|-----|
| Define tool | `@tool(name, description, schema)` |
| Bundle tools | `create_sdk_mcp_server(name, version, tools=[...])` |
| Register server | `ClaudeAgentOptions(mcp_servers={"name": server})` |
| Pre-approve | `allowed_tools=["mcp__name__toolname"]` |
| Tool callable name | `mcp__<server>__<tool>` |
| Error return | `{"content": [...], "is_error": True}` |
