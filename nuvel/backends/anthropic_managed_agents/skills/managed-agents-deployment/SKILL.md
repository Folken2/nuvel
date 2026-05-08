---
name: managed-agents-deployment
description: Deploying a Managed Agents project to production — wrapping the SDK in FastAPI with SSE, the env vars that have to be set, agent-version pinning, bearer auth, cost tracking via span events, and Docker/Railway/Fly. Read when going from "setup.py worked locally" to "it's running on a server", when designing the request/response contract, when investigating why deployments lose state, or when planning rollouts of agent updates.
---

# Deploying a Managed Agents project

A nuvel-scaffolded Managed Agents project has a clean deploy story: the agent runs on Anthropic's infrastructure, your service is a thin proxy. That changes what you have to think about. This skill covers the parts that aren't obvious.

## What lives where

| Concern | Lives in | Notes |
|---|---|---|
| Agent loop, model inference, sandboxing, cost | Anthropic | Free of you to manage |
| Agent ID, environment ID, agent version | Your env vars | Persisted by `setup.py` to `.env` |
| Custom-tool handlers | Your server process | The only place secrets for non-MCP services can live |
| MCP credentials | Anthropic vaults | OAuth refresh handled by Anthropic |
| Session history | Anthropic | Retrievable via `events.list` for audits |

You ship a thin FastAPI app. The heavy lifting is elsewhere.

## Required env vars

| Variable | Where it comes from |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic account |
| `MANAGED_AGENT_ID` | `setup.py` (returned by `agents.create()`) |
| `MANAGED_AGENT_ENV_ID` | `setup.py` (returned by `environments.create()`) |
| `MANAGED_AGENT_VERSION` | `setup.py` (the current version of the agent) |

The nuvel scaffold's `setup.py` writes the last three to `.env` after applying the YAMLs. For deployment, copy them from your local `.env` to the platform's environment configuration (Railway/Fly/Vercel/etc.) — they're stable across runs unless you re-run `setup.py`.

## Agent-version pinning in production

`MANAGED_AGENT_VERSION` is the current head version after the last `setup.py` run. Two operational patterns:

**Track latest (default).** `sessions.create(agent=AGENT_ID, ...)` uses the current head. New sessions automatically pick up `setup.py` changes. Good for fast iteration; risky for traffic that needs reproducibility.

**Pin explicitly.** `sessions.create(agent={"type": "agent", "id": AGENT_ID, "version": int(VERSION)}, ...)`. New sessions stay on a known version even if someone runs `setup.py` mid-flight. Good for prod; lets you roll forward in CI by re-running `setup.py` and bumping `MANAGED_AGENT_VERSION`.

The nuvel scaffold's `orchestrator.py` defaults to tracking latest. To pin, change the `agent=` argument to the dict form and read `MANAGED_AGENT_VERSION` from env.

## The request/response contract

The default scaffold:

```
POST /run_sse/
Authorization: Bearer <API_KEY>     (if API_KEY env is set)
Content-Type: application/json
{ "prompt": "..." }

Response: text/event-stream
data: {"type": "session.created", "session_id": "sesn_..."}
data: {"type": "agent.message", "content": [...]}
data: {"type": "agent.tool_use", "name": "read", ...}
data: {"type": "session.status_idle", "stop_reason": {"type": "end_turn"}}
data: [DONE]
```

Frontends store the `session_id` from the first event so they can correlate later API calls (events.list for audit, files.list for outputs).

## One session per request

Don't reuse a single session across HTTP requests for different users — each session is a separate conversation thread. Create a fresh session per request:

```python
@app.post("/run_sse/")
async def run_sse(body: dict):
    return StreamingResponse(_sse_stream(body["prompt"]))

async def _sse_stream(prompt: str):
    # Inside the generator: create + stream + tear down per request.
    ...
```

If you want **multi-turn for one user** (e.g. chat), pass the previous `session_id` in subsequent requests and reuse it via `events.send` + `events.stream` against the same session. The session's history is preserved server-side; you don't have to send it back.

## Bearer auth

The scaffold's pattern: if `API_KEY` env is set, require `Authorization: Bearer <API_KEY>` on `/run_sse/`. If unset, no auth (good for local dev). Don't reach for OAuth/JWT until there's a real reason — most internal-tool agents are fine with one shared bearer behind a VPN or Cloudflare Access.

For multi-tenant deploys, put auth in front of the FastAPI app (Cloudflare Access, an API gateway) — not inside it. The agent server should be a dumb relay.

## Cost tracking

Per-turn cost lands on `span.model_request_end` events:

```python
if event.type == "span.model_request_end":
    usage = event.model_usage  # {input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens}
    persist_usage(session.id, usage)
```

Per-session aggregate is on `client.beta.sessions.retrieve(session.id).usage` after the session ends. For multi-tenant cost attribution, persist per-event usage to a database keyed by `session.id` and your tenant ID.

The `MAX_BUDGET_USD` Claude Agent SDK pattern doesn't apply here — Managed Agents don't expose a hard per-session budget. If you need a cap, enforce it at your application layer: subscribe to `span.model_request_end`, sum cost, and `client.beta.sessions.events.send` an `interrupt` if you exceed your threshold.

## Reconnecting under load

A long-lived agent process (cron job, websocket bridge) will eventually drop a stream. Always implement the consolidation pattern from `managed-agents-events` — fetch history on (re)connect, dedupe by event ID, then resume the live stream.

The nuvel scaffold's `server.py` doesn't implement reconnection because the FastAPI request lifecycle is short-lived (one request, one stream). If you're building a long-running consumer, add the pattern.

## Docker + Railway + Fly

The scaffold ships a `Dockerfile` (python:3.12-slim, copies the project, runs `python server.py`) and `railway.json` (Dockerfile builder, `/health` healthcheck). Portable to Fly or Cloud Run with a `fly.toml` or service yaml.

**Env var hygiene at build time.** Don't bake `ANTHROPIC_API_KEY`, `MANAGED_AGENT_ID`, etc. into the Docker image — set them at the platform level. The image stays generic; the env varies per deploy.

**Health check.** `/health` returns 200 with the agent name. Wire it to your platform's healthcheck (`healthcheckPath: /health` in `railway.json` already does this).

**Cold starts.** The Anthropic client doesn't pre-warm. First request after a cold start has ~100-200ms extra latency. Acceptable for most use cases; if it isn't, keep one container warm via a low-frequency cron pinging `/health`.

## Updating the agent without downtime

Edit `agent.yaml`, run `setup.py` — this updates the agent in place (creates a new version) and bumps `MANAGED_AGENT_VERSION` in `.env`. Existing sessions running on older versions continue undisturbed. New sessions pick up the new version (default behavior, since the scaffold tracks latest).

For pinned production, the rollout is two steps:
1. Re-run `setup.py` against your prod environment (creates new version N+1, but pinned sessions still use N).
2. Update `MANAGED_AGENT_VERSION` in your platform's env config and restart. New sessions now use N+1.

If N+1 regresses, set the env var back to N and restart — instant rollback.

## Pre-launch checklist

- [ ] `ANTHROPIC_API_KEY`, `MANAGED_AGENT_ID`, `MANAGED_AGENT_ENV_ID` set in platform env
- [ ] `API_KEY` set if `/run_sse/` is internet-facing
- [ ] `/health` healthcheck wired to the platform
- [ ] Reconnect/consolidation pattern in any long-running consumer (not the FastAPI scaffold by default)
- [ ] Decide: latest-tracking or pinned versions for production traffic
- [ ] If multi-tenant: auth in front of the app, per-request session creation
- [ ] If cost-sensitive: span.model_request_end persistence + per-session budget enforcement
