# Pick a backend

Three backends, three different sweet spots.

| Backend | Pick it when… |
|---|---|
| **Google ADK** *(default)* | You want production session storage out of the box (Postgres), FastAPI surface, plugin system, ADK's WebSocket streaming. Most batteries included. Default for `nuvel new`. |
| **Claude Agent SDK** | You want Anthropic's first-party agent loop, async streaming via `receive_response()`, fewer infra primitives, lower-level control over the agent's turn cycle. |
| **Anthropic Managed Agents** | You don't want to operate session state at all — Anthropic owns the runtime. Smallest infra footprint; least flexibility. |

## Concrete differences

### Session / state

- **ADK** has `BaseSessionService` with in-memory and Postgres implementations. Sessions persist across restarts in production. ADK is the only backend that handles long-running user conversations cleanly.
- **Claude Agent SDK** has session IDs but state is a thin wrapper — you persist what you need yourself.
- **Anthropic Managed Agents** stores session state server-side. You get session IDs and resumption for free, but you can't introspect or rewind.

### Tool-calling shape

- **ADK** — Python tools registered on `LlmAgent(tools=[...])`. ADK normalizes calling conventions. Composio MCP toolset is supported.
- **Claude Agent SDK** — tool definitions in JSON Schema, callbacks dispatched per turn.
- **Anthropic Managed Agents** — tools defined when you register the managed agent; runtime calls are server-side.

### Streaming

- **ADK** — WebSocket-based, opt-in via `STREAMING_ENABLED=true` (overrides model to Gemini for live streaming).
- **Claude Agent SDK** — first-class. `client.receive_response()` is an async iterator over the agent's reply stream.
- **Anthropic Managed Agents** — server-side; you receive events through the managed-agent API.

### nuvel feature support

| Feature | ADK | Claude SDK | Anthropic Managed |
|---|---|---|---|
| `--persona` | ✅ | ❌ | ❌ |
| `--with-composio` | ✅ | ❌ | ❌ |
| `--with-slack` / `--with-telegram` / `--with-teams` | ✅ | ❌ | ❌ |
| `--workflow` | ✅ | ❌ | ❌ |
| Bundled skills | ✅ | ✅ | ✅ |
| Generated FastAPI server | ✅ | ✅ | ✅ |
| Production Postgres sessions | ✅ | manual | server-side |
| Railway-ready Dockerfile | ✅ | ✅ | ✅ |

The advanced bundles (persona, composio, channels) are ADK-only today because that's where the runtime primitives are richest. The other two backends accept the flags but exit with a "not yet supported" error, so a future you can find them via `nuvel new --help`.

## When to switch

You can scaffold a new agent on a different backend without disturbing existing agents — they don't share runtime. There's no built-in "migrate" path; the prompt and skills are portable, but session data is not.

## Default

If you have no strong reason to pick another, **stay on ADK**. It's the most-tested path through nuvel and the one that gets new features first.
