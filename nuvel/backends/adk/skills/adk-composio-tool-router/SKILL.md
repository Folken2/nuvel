---
name: adk-composio-tool-router
description: The Composio Tool Router for ADK agents — the --with-composio scaffold flag, per-user sessions via composio.create(user_id=...), the McpToolset wiring, multi-tenant patterns, and when not to reach for it. Read when an ADK agent needs broad integration coverage (Gmail, GitHub, Slack, Notion, Calendar, Linear, etc.) without authoring per-service tools, when the user wants ~1000 toolkits behind one endpoint, or when adding multi-tenant credential isolation to an agent.
---

# Composio Tool Router for ADK agents

Composio is a hosted MCP server that exposes ~1000 third-party toolkits — Gmail, GitHub, Slack, Notion, Linear, Calendar, Stripe, HubSpot, every major SaaS — behind a single MCP endpoint. Composio handles auth (OAuth flows, token refresh, per-user credentials), tool discovery (the agent sees only the toolkits you've connected), and execution. You wire it up once; your agent gains broad integration coverage without authoring per-service tools.

`nuvel new <name> --with-composio` ships with this already wired. This skill explains what that flag actually does and how to operate it.

## What `--with-composio` ships

The overlay drops one file into your agent: `<package>/tools/composio_mcp.py`, exporting `build_composio_mcp_toolset()`. The agent's `tools/__init__.py` calls it at startup; if `COMPOSIO_API_KEY` is set, the agent gains a `McpToolset` wrapping Composio's hosted MCP server.

```python
# Roughly what runs at agent startup
composio = Composio()
session = composio.create(user_id=os.getenv("COMPOSIO_USER_ID", "default"))
return McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=session.mcp.url,
        headers=session.mcp.headers,
    ),
)
```

If `COMPOSIO_API_KEY` is unset, the toolset gracefully no-ops and the agent starts with only its local tools. The flag is safe to ship; deployments without the key just don't get the integrations.

## The two env vars

| Variable | Required | What it does |
|---|---|---|
| `COMPOSIO_API_KEY` | Yes (to enable) | Your Composio account key. Get it at <https://app.composio.dev>. |
| `COMPOSIO_USER_ID` | No (default: `"default"`) | Identity scope for the session — see Multi-tenant section below. |

In single-tenant agents you can leave `COMPOSIO_USER_ID` unset and everything works. In multi-tenant agents you'll want to thread the real user ID into this — see below.

## Connecting a toolkit (one-time, in the dashboard)

The agent doesn't know about Gmail until you've connected your Composio account to Gmail. This is a one-time human step:

1. Go to <https://app.composio.dev/apps>
2. Pick a toolkit (Gmail, GitHub, Slack, etc.) → Connect
3. Complete the OAuth flow as the user the agent will act on behalf of (or as a service account for shared workflows)

After this, the toolkit's tools appear in the MCP session and the agent can call them. Composio refreshes tokens automatically; you don't manage them.

The toolkits you've connected are scoped by `user_id`: a session created with `user_id="alice"` sees `alice`'s connections, not `bob`'s. This is the multi-tenant primitive.

## How Composio tools appear to the agent

Tool names follow MCP conventions — Composio prefixes them by toolkit. Examples:

- `GMAIL_SEND_EMAIL`
- `GITHUB_CREATE_ISSUE`
- `SLACK_POST_MESSAGE`
- `NOTION_CREATE_PAGE`
- `LINEAR_CREATE_ISSUE`

The ADK `McpToolset` wires these into the agent's tool list automatically; your system prompt doesn't need to enumerate them. Claude/the model picks based on the descriptions Composio provides.

If you want only specific toolkits exposed (rather than every connection), filter at the Composio dashboard level — connect only what the agent should access. The MCP transport doesn't have per-tool gating.

## Multi-tenant patterns — `user_id` is load-bearing

In a single-tenant agent (you, your tools), `COMPOSIO_USER_ID="default"` and one set of OAuth connections is fine.

In a multi-tenant deployment (your agent acts on behalf of N end-users), each user needs their own Composio connections so OAuth tokens don't cross users. Two patterns:

**1. Per-request user_id (clean, requires harness changes).** Don't use the env var. Build the toolset per request, threading the authenticated user's ID:

```python
def get_agent_for_user(end_user_id: str):
    composio = Composio()
    session = composio.create(user_id=end_user_id)
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=session.mcp.url, headers=session.mcp.headers,
        )
    )
    return LlmAgent(tools=[*local_tools, toolset], ...)
```

This means each session has its own connections; user A can't trigger Gmail calls against user B's account. The cost is creating a fresh `Composio.create()` session per request — usually fine, since session creation is fast.

**2. Service-account `user_id` (simpler, less isolated).** Set `COMPOSIO_USER_ID="service-account"` and connect every toolkit once under that identity. All tenants share one set of connections. Use only when the agent acts on its own data, not on the end-user's.

## Cost model

Composio's pricing is per tool execution, not per agent or per session. You're paying for the integrations, not the routing. Check <https://composio.dev/pricing> for current numbers — there's a generous free tier for prototyping.

The hosted MCP endpoint is free to *connect to*; you only pay when the agent actually calls a tool.

## When NOT to use Composio

Composio is the right answer when the agent needs broad coverage. It's the wrong answer when:

- **You only need 1-2 integrations.** Writing direct tools for Gmail-only or GitHub-only is less indirection, less cost, and gives you tighter control over rate limiting and error handling. Use Composio when you genuinely need breadth.
- **The integration has tight latency requirements.** Composio adds an MCP hop (your agent → Composio's MCP server → the third-party API). For most workflows this is irrelevant; for sub-100ms-budget tool calls, write direct.
- **You need bespoke business logic on top of the integration.** Composio's tools are generic ("send email", "create issue"). If your agent should "send email AND log it in our CRM AND notify Slack," compose those as a custom function tool that calls Composio's tools internally — or skip Composio for that path.
- **Compliance / data residency concerns.** Composio is a hosted service; tool inputs and outputs flow through their infrastructure. If your data can't leave your boundary, you need self-hosted MCP servers or direct integration.

## Common patterns

For deeper patterns — multi-tenant request routing, service-account vs end-user-account models, Composio with `--persona` agents (where the persona's "skills" can include managing connections), filtering toolkits per agent, and debugging "tool not found" errors — see `references/composio-patterns.md`.

## Quick reference

```bash
# Scaffold with Composio
nuvel new my-agent --with-composio --description "..."

# Configure (in .env)
COMPOSIO_API_KEY=ck_...
COMPOSIO_USER_ID=default       # or per-request, see Multi-tenant

# Connect toolkits (one-time, in the dashboard)
# https://app.composio.dev/apps → pick → Connect → OAuth

# Run
python run_adk.py
```

```python
# To bypass the env-var pattern and create per-user sessions in code:
from composio import Composio
session = Composio().create(user_id=end_user_id)
# Use session.mcp.url and session.mcp.headers in McpToolset
```

| Concept | API |
|---------|-----|
| Scaffold flag | `--with-composio` (ADK only) |
| Session creation | `composio.create(user_id=...)` |
| MCP endpoint | `session.mcp.url` + `session.mcp.headers` |
| ADK wiring | `McpToolset(connection_params=StreamableHTTPConnectionParams(...))` |
| Tool naming | `<TOOLKIT>_<ACTION>` (e.g. `GMAIL_SEND_EMAIL`) |
| Per-user isolation | Different `user_id` per Composio session |
