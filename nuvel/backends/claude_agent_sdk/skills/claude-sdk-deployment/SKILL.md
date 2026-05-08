---
name: claude-sdk-deployment
description: Deploying Claude Agent SDK agents to production — wrapping the SDK in FastAPI with SSE streaming, session resume, cost tracking via ResultMessage, Docker/Railway/Fly config, ANTHROPIC_API_KEY handling, and bearer-auth patterns. Read when going from "it works locally" to "it's running on a server", when designing the request/response contract for an agent endpoint, or when investigating why deployments are slower or more expensive than expected.
type: knowledge
---

# Deploying a Claude Agent SDK agent

The SDK gives you `ClaudeSDKClient`, which is async-context-manager + async-iterator. It does not give you a server. The two questions deployment answers:

1. **What's the request/response contract?** (HTTP path, auth, streaming format)
2. **What does the server do with the SDK?** (per-request client, session state, cost tracking)

The nuvel-scaffolded `server.py` answers both with a FastAPI + SSE shape that's familiar to people who've deployed ADK or other agent runtimes. This skill explains why that shape was chosen and how to extend it.

## The default scaffold's contract

```
POST /run_sse/
Authorization: Bearer <API_KEY>      (if API_KEY env is set)
Content-Type: application/json
{ "prompt": "..." }

Response: text/event-stream
data: {"type": "assistant", "content": [{"type": "text", "text": "..."}]}
data: {"type": "assistant", "content": [{"type": "tool_use", "name": "...", "input": {...}}]}
data: {"type": "result", "session_id": "...", "total_cost_usd": 0.0042, "duration_ms": 4200, "num_turns": 3}
data: [DONE]
```

Each SSE event is one Claude SDK message serialized as JSON. The frontend can render text incrementally, surface tool calls as they happen, and capture cost on the final `result` event.

## One client per request

Don't reuse a single `ClaudeSDKClient` across requests. The async context manager is the unit of session — entering it starts a session, exiting it tears down the subprocess transport and any MCP server children. Build a fresh client per request:

```python
async def _sse_stream(prompt: str):
    async with get_client() as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            yield serialize(msg)
```

Costs about 100ms of startup per request. If that matters for your latency budget, you'll want a client pool — but most agents are several-second responses where 100ms doesn't move the needle.

## Session resume (multi-turn from a stateless server)

A stateless HTTP server can still run multi-turn agents by threading `session_id`:

```python
@app.post("/run_sse/")
async def run_sse(body: dict):
    options = build_options()
    options.resume = body.get("session_id")  # None on first turn
    options.fork_session = False              # continue, don't fork

    async with ClaudeSDKClient(options=options) as client:
        await client.query(body["prompt"])
        async for msg in client.receive_response():
            yield serialize(msg)
```

The frontend keeps the `session_id` from the `result` event of turn N and sends it in turn N+1. The SDK loads conversation history transparently.

For multi-tenant deployments where session storage shouldn't live on the SDK's filesystem, use `session_store` with a `PostgresSessionStore` or similar — the SDK exposes a session-store interface for this.

## Cost tracking

The `ResultMessage` at the end of every turn includes `total_cost_usd` (cumulative for the session) and `num_turns`. Two patterns:

**Per-turn cost** = `total_cost_usd_now - total_cost_usd_previous_turn`. Subtract across turns yourself if you want per-turn billing.

**Per-session cap** = `max_budget_usd` in `ClaudeAgentOptions`. The SDK enforces it; once exceeded, further LLM calls fail. No plugin needed.

**Per-tenant aggregation** = persist the `result` event to a tracing table (the nuvel scaffold writes JSONL via `traces/trace_writer.py`) and aggregate at query time. Real production systems use a `total_cost_usd` column on a `sessions` table updated on each turn.

Don't try to match LiteLLM-style per-call pricing — the SDK gives you cumulative cost per session and that's enough for 95% of cases.

## ANTHROPIC_API_KEY handling

The SDK reads `ANTHROPIC_API_KEY` from the environment. You don't pass it explicitly; the underlying CLI does the lookup. Two operational patterns:

**Single-key deploys** — set `ANTHROPIC_API_KEY` in the deployment env (Railway dashboard, Fly secrets, etc.). The agent uses one key for all users.

**Per-tenant keys** — set `env={"ANTHROPIC_API_KEY": tenant_key}` in `ClaudeAgentOptions`. The SDK launches its subprocess transport with that env, so each session uses its own key. Right when you're charging tenants directly.

## Bearer auth on /run_sse/

The scaffold's pattern: if `API_KEY` env is set, require `Authorization: Bearer <API_KEY>`. If unset, no auth (good for local dev). Don't reach for OAuth or JWT until you have a real reason — most internal-tool agents are fine with a single shared bearer behind a VPN or Cloudflare Access.

When you do need real auth (multi-tenant, public), put it in front of the FastAPI app — not inside it. Cloudflare Access, an API gateway, or a simple JWT-validating proxy are all better than building auth into the agent server.

## Docker + Railway + Fly

The scaffold ships with `Dockerfile` (python:3.12-slim, copies the project, runs `python server.py`) and `railway.json` (starts via the Dockerfile, healthcheck on `/health`). For Fly, drop a `fly.toml` next to it; the Dockerfile is portable.

One Docker subtlety: the Claude Agent SDK spawns a Node.js subprocess (the underlying CLI). The slim base image works because `claude-agent-sdk` ships its own bundled Node — but if you switch to `python:3.12-alpine` you'll likely hit glibc issues. Stay on slim.

## Common deployment mistakes

- **Reusing a single `ClaudeSDKClient` across HTTP requests.** Eventually leaks subprocesses or stalls. Always per-request.
- **Forgetting `permission_mode`.** Default is `default` (interactive). Server deploys need `acceptEdits` or `dontAsk`. See `claude-sdk-permissions`.
- **No `max_budget_usd`.** A bug or prompt injection can cause runaway costs. Set a per-session cap; it's free and prevents disasters.
- **Sending tool internals to the client.** Serialize tool args carefully — they sometimes contain secrets (API keys, file contents) that shouldn't reach the browser.
- **Using `bypassPermissions` to skip prompts.** Lock-down with `dontAsk` + explicit `allowed_tools` instead.

## Quick checklist

Before merging a Claude Agent SDK agent to production:

- [ ] `permission_mode` is `acceptEdits` or `dontAsk`, not `default`
- [ ] `max_budget_usd` is set
- [ ] `ANTHROPIC_API_KEY` is in the deploy environment, not committed
- [ ] `API_KEY` is set if `/run_sse/` is internet-facing
- [ ] `/health` returns 200 and is wired to the platform's healthcheck
- [ ] Trace writer is on (`TRACE_ENABLED=true`) so you can debug after the fact
- [ ] One `ClaudeSDKClient` per request, not a singleton
