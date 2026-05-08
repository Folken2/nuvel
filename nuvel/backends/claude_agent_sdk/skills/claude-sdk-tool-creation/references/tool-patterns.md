# Claude Agent SDK tool patterns

Deeper patterns for tools in the Claude Agent SDK. Read after `SKILL.md` when you have a real tool to build and want to know the right shape.

## Multi-content responses

A tool's `content` array can hold multiple blocks. Use this when one logical result has multiple natural representations:

```python
@tool("query_db", "Run a SQL query", {"sql": str})
async def query_db(args):
    rows = await db.fetch(args["sql"])
    return {
        "content": [
            {"type": "text", "text": f"Returned {len(rows)} rows."},
            {"type": "text", "text": format_table(rows)},
        ],
    }
```

Two blocks is usually enough — the first as a summary, the second as the data. Claude reads both; the summary often makes it cite results without quoting the whole table back.

## Long-running tools (sub-second to a few seconds)

Tools are async — `await` away. The SDK serializes tool calls within a turn, so a 10-second tool blocks the entire agent for 10 seconds. That's usually fine. Two cases where it isn't:

1. **Genuinely long operations** (file conversion, deploys). Don't run inline — kick off and return a job id. Provide a separate `check_status` tool. The agent learns the pattern.
2. **Parallelizable batch work**. Don't call the same tool 50 times in a loop. Give the tool a list parameter and use `asyncio.gather` internally.

## Sharing state across tool calls

The SDK doesn't give tools a per-session state object. Three workable patterns:

```python
# Module-level cache (process-lifetime, not session-lifetime)
_cache: dict[str, Any] = {}

@tool(...)
async def lookup(args):
    if args["key"] not in _cache:
        _cache[args["key"]] = await expensive_fetch(args["key"])
    return {"content": [{"type": "text", "text": _cache[args["key"]]}]}
```

```python
# Closure over per-session state (build the tool inside a factory)
def build_tools(session_id: str):
    state = SessionState(session_id)

    @tool(...)
    async def remember(args):
        state.set(args["key"], args["value"])
        return {"content": [{"type": "text", "text": "saved"}]}

    return create_sdk_mcp_server(name="tools", tools=[remember])
```

```python
# External store (Redis, SQLite). Always works; pay the I/O cost.
@tool(...)
async def remember(args):
    await redis.set(f"sess:{session_id}:{args['key']}", args["value"])
    return {"content": [{"type": "text", "text": "saved"}]}
```

Pick the simplest pattern that works for your concurrency and persistence needs. The closure form is the cleanest in single-tenant deployments.

## Validation that fails informatively

The SDK validates against the schema before invoking your function — type mismatches never reach you. Beyond types, validate semantically inside the tool and return clear errors:

```python
@tool("send_email", "Send an email", {"to": str, "subject": str})
async def send_email(args):
    if "@" not in args["to"]:
        return {
            "content": [{"type": "text", "text": f"Invalid email: {args['to']}"}],
            "is_error": True,
        }
    if len(args["subject"]) > 200:
        return {
            "content": [{"type": "text", "text": "Subject too long (max 200 chars)"}],
            "is_error": True,
        }
    # ... actual send ...
```

Claude reads the error text and corrects on the next turn. Vague errors ("Bad input") result in vague retries.

## Testing tools without an LLM

Each `@tool` function is a regular async callable wrapped with metadata. You can call it directly:

```python
import pytest
from my_agent.tools.example import echo

@pytest.mark.asyncio
async def test_echo():
    result = await echo({"text": "hi"})
    assert result["content"][0]["text"] == "echo: hi"
```

This is the right level for unit testing — you assert the contract you control. Don't try to mock `ClaudeSDKClient` to test that Claude calls your tool correctly; that's an integration test and the SDK's MCP transport will fight you. Trust Claude to call the tool when the description is good.

## Tool descriptions are prompts

The second argument to `@tool` ends up in Claude's tool list. It's prompt-engineering surface, not documentation:

```python
# Bad — describes the function
@tool("get_user", "Returns user data", {"id": str})

# Better — describes when to use it
@tool("get_user", "Look up a user's profile and recent activity by id. Use when you need to verify identity or check their last actions.", {"id": str})
```

Two sentences max: what it does + when to reach for it. Verbose descriptions hurt because they fill the context window; thin descriptions hurt because Claude can't choose the right tool.

## Common bugs

- **`allowed_tools=["my_tool"]`** — wrong; use `mcp__server__my_tool`.
- **Raising in tools** — Claude never sees it. Always return a structured error.
- **Mutating shared state without locks** — async tools can interleave; use `asyncio.Lock` if mutation matters.
- **Returning bytes / non-JSON-serializable types** — the SDK serializes content blocks; make sure everything in `content` is JSON-safe.
- **Schema mismatch with function signature** — the SDK trusts the schema; if the function uses a key the schema doesn't declare, the tool gets an empty value at runtime.
