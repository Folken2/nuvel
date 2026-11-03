# Pick a backend

Three backends, three different sweet spots.

| Backend | Pick it when… |
|---|---|
| **Google ADK** *(default)* | You want production session storage out of the box (Postgres), FastAPI surface, plugin system, ADK's WebSocket streaming. Most batteries included. Default for `nuvel agent create`. |
| **Claude Agent SDK** | You want Anthropic's first-party agent loop, async streaming via `receive_response()`, fewer infra primitives, lower-level control over the agent's turn cycle. |
| **Hermes Agent** | You already run Hermes, or want a configured agent rather than a program: the output is a profile (`SOUL.md`, `config.yaml`, skills) and Hermes owns the runtime. Smallest footprint; least flexibility. |

## Concrete differences

### Session / state

- **ADK** has `BaseSessionService` with in-memory and Postgres implementations. Sessions persist across restarts in production. ADK is the only backend where nuvel generates this for you.
- **Claude Agent SDK** has session IDs but state is a thin wrapper — you persist what you need yourself.
- **Hermes Agent** owns session state. nuvel generates no session code for it.

### Tool-calling shape

- **ADK** — Python tools registered on `LlmAgent(tools=[...])`. ADK normalizes calling conventions. Composio MCP toolset is supported.
- **Claude Agent SDK** — async functions decorated with the SDK's `@tool` under `tools/`, dispatched by the SDK during the turn.
- **Hermes Agent** — no generated tool code; behaviour comes from the profile's `config.yaml` and skills.

### Streaming

- **ADK** — WebSocket-based, opt-in via `STREAMING_ENABLED=true` (overrides model to Gemini for live streaming).
- **Claude Agent SDK** — first-class. `client.receive_response()` is an async iterator over the agent's reply stream.
- **Hermes Agent** — handled by the Hermes runtime; nuvel generates no streaming code.

### nuvel feature support

| Feature | ADK | Claude SDK | Hermes |
|---|---|---|---|
| `--persona` | ✅ | ❌ | ❌ |
| `--with-composio` | ✅ | ❌ | ❌ |
| `--with-slack` / `--with-teams` | ✅ | ❌ | ❌ |
| `--with-telegram` | ✅ | ❌ | ✅ |
| `--workflow` | ✅ | ❌ | ❌ |
| `--with-eval` | ✅ | ❌ | ❌ |
| `--with-litellm` | ✅ | ❌ | ❌ |
| Knowledge skills (`nuvel skills list`) | 15 | 6 | 1 |
| Generated FastAPI server | ✅ | ✅ | ❌ |
| Production Postgres sessions | ✅ | manual | owned by Hermes |
| Railway-ready Dockerfile | ✅ | ✅ | ❌ |

This table is the one place that states which flags each backend honours; other pages mark a flag *(adk only)* or *(adk, hermes)* and link here.

Every flag above except `--with-telegram` is ADK-only, because that's where the runtime primitives are richest. `--with-telegram` is the one flag with a second backend, and it does a different job on each: ADK gets a generated webhook gateway, while Hermes gets `platforms.telegram.enabled: true` in `config.yaml` and the Hermes runtime runs the gateway. A backend refuses a flag it doesn't support rather than silently ignoring it.

## When to switch

You can scaffold a new agent on a different backend without disturbing existing agents — they don't share runtime. There's no built-in "migrate" path. Your prompt text and the `SKILL.md` format carry over, but bundled skills are written per backend and session data does not move.

## Default

If you have no strong reason to pick another, **stay on ADK**. It's the most-tested path through nuvel and the one that gets new features first.
