---
name: managed-agents-overview
description: The mandatory flow for Anthropic Managed Agents — create a versioned agent once, reference it from sessions on every run. Read first when starting a Managed Agents project, when migrating from a different Anthropic surface (Claude API, Claude Agent SDK), or when confused about why session.create() rejects model/system/tools fields.
---

# Anthropic Managed Agents — the mandatory flow

Managed Agents is Anthropic's hosted agent runtime. The agent loop runs on Anthropic's orchestration layer; per-session containers host the workspace where the agent's tools execute. You don't run the loop, the model, or the container — you create the configuration and the sessions, then consume the event stream.

This skill is the foundation for every other Managed Agents skill in the bundle. Internalize it first.

## The flow that's not optional

```
┌─ setup (once) ─────────┐     ┌─ runtime (every invocation) ─┐
│ agents.create()        │     │ sessions.create(             │
│   → store agent_id     │ ──→ │   agent={type:..., id: ID}   │
│     in config/env/db   │     │ )                            │
└────────────────────────┘     └──────────────────────────────┘
```

| Field | Lives on | Notes |
|---|---|---|
| `model`, `system`, `tools`, `mcp_servers`, `skills` | **agent** | Set on `agents.create()`. Trying to put them on `sessions.create()` returns 400. |
| `agent`, `environment_id`, `resources`, `vault_ids`, `title` | session | Set on `sessions.create()`. The `agent` field accepts only an ID string or `{type: "agent", id, version}`. |

If your code calls `agents.create()` at the top of every script run, that's the #1 anti-pattern — it accumulates orphaned agents and pays create latency for nothing. Hoist agent creation to a one-time setup step (the nuvel scaffold's `setup.py` does this) and persist the ID.

## Why agents are versioned

Every `POST /v1/agents/{id}` (an update) creates a new immutable version. Sessions can pin to a version: `{type: "agent", id, version: 7}`. This buys you:

- **Reproducibility.** A reported bug in production session #4392 came from agent version 7. You can spin up a new session pinned to version 7 and reproduce.
- **Safe iteration.** Deploy a new system prompt; existing sessions keep running on the old version, new sessions use the new one. If the new prompt regresses, pin new sessions back while you debug.
- **A/B testing.** Two cohorts on two versions; same agent ID.

If you bare-string the `agent` field (`agent="agent_abc123"`), sessions track the latest version at creation time — fine for most uses, not fine when you need reproducibility.

## Control plane vs data plane

A useful split for thinking about deployment:

| Plane | What it is | Where it lives | Cadence |
|---|---|---|---|
| **Control plane** | Agents, environments, vaults — the configuration | YAML files in your repo, applied via `setup.py` or `ant` CLI | One-time / per-deploy |
| **Data plane** | Sessions, events, tool calls — the actual work | SDK code in your application (FastAPI server, cron, etc.) | Per request / per turn |

The nuvel-scaffolded project encodes this split: `agent.yaml` + `environment.yaml` + `setup.py` are control plane; `server.py` + `orchestrator.py` are data plane.

For team workflows, prefer the [`ant` CLI](https://platform.claude.com/docs/en/api/sdks/cli.md) for the control plane — it handles `create` / `update --version N` / archive lifecycle from CI against the YAML files.

## Beta headers

`managed-agents-2026-04-01` is required and **the SDK sets it automatically** for all `client.beta.{agents,environments,sessions,vaults,memory_stores}.*` calls. You don't pass it manually. Two exceptions:

- **`client.beta.files.list({scope_id: session.id})`** — pulls session output files. The SDK adds the Files header (`files-api-2025-04-14`) automatically; you must explicitly add `betas=["managed-agents-2026-04-01"]` since it's the Files endpoint accepting a Managed Agents parameter.
- **Skills API** (`client.beta.skills.*`) — uses `skills-2025-10-02`, set automatically.

## The pieces in one diagram

```
                       ┌─────────────────────────────────────┐
                       │  Anthropic orchestration layer      │
agents.create() ──────▶│  (agent loop: Claude + tool calls)  │
(config + version)     └──────────────┬──────────────────────┘
                                      │ tool calls
                                      ▼
environments.create() ─▶ Container ───┤
(template)                            │
                                      │
                                      └── sessions.create()
                                          (event stream in/out)
```

You author the agent and environment as YAML, apply them once via `setup.py` (or `ant`), and persist the IDs. Your application code only ever calls `sessions.create()` and consumes the event stream.

## Common confusions

- **"Why can't I pass `model` to `sessions.create()`?"** Because the agent owns the model. Update the agent (creates a new version) or pin to a different agent.
- **"Where do API keys for Stripe / GitHub REST / Slack go?"** Not in the container — there's no env var injection, and vaults are MCP-only. The right answer is a custom tool handled host-side. See `managed-agents-tools`.
- **"My session never ends."** You're probably breaking on `session.status_idle` alone. Idle is transient when the agent is awaiting tool confirmations or custom-tool results. See `managed-agents-events`.
- **"The first events of my session arrived as one batch."** Stream-first ordering — open the SSE stream **before** sending the kickoff event. See `managed-agents-events`.
- **"Archive feels safe — let me clean up."** Archive on agents, environments, and memory stores is **permanent** with no unarchive. Existing sessions continue but new sessions cannot reference an archived resource. Sessions and vault credentials archive freely; the persistent resources don't. Always confirm before archiving production resources.

## Quick reference

```python
from anthropic import Anthropic

client = Anthropic()  # reads ANTHROPIC_API_KEY

# ── setup (once) ──────────────────────────────────────────────
env   = client.beta.environments.create(name="my-env", config={"type": "cloud", "networking": {"type": "unrestricted"}})
agent = client.beta.agents.create(
    name="My Agent",
    model="claude-opus-4-7",
    system="You are a helpful assistant.",
    tools=[{"type": "agent_toolset_20260401"}],
)
# Persist agent.id and env.id somewhere durable.

# ── runtime (every request) ───────────────────────────────────
session = client.beta.sessions.create(agent=agent.id, environment_id=env.id)
client.beta.sessions.events.send(
    session_id=session.id,
    events=[{"type": "user.message", "content": [{"type": "text", "text": "Hello"}]}],
)
with client.beta.sessions.events.stream(session_id=session.id) as stream:
    for event in stream:
        ...  # process events
```

Read `managed-agents-events` next for the streaming gotchas, then `managed-agents-tools` to learn how to wire prebuilt tools, MCP servers, and host-side custom tools.
