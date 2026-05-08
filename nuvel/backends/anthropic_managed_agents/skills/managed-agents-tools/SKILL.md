---
name: managed-agents-tools
description: The three kinds of tools in Anthropic Managed Agents — the prebuilt agent toolset (`agent_toolset_20260401`), MCP toolsets with vault-backed credentials, and custom tools handled host-side. Read when adding a new capability to your agent, when deciding where to put a third-party API call, when a third-party service has a hosted MCP server, or when an integration needs an API key.
---

# Tools in Managed Agents

Three categories. Pick by where the work runs and where the credentials live.

| Type | Runs where | Credentials | Use for |
|---|---|---|---|
| **Prebuilt agent toolset** (`agent_toolset_20260401`) | Anthropic container | None — built in | File I/O, bash, web search/fetch — the basics every agent needs |
| **MCP toolset** (`mcp_toolset`) | Anthropic container, calling MCP server | Vault (managed by Anthropic, auto-refreshed) | Hosted integrations: GitHub, Linear, Notion, Asana, etc. |
| **Custom tool** (`custom`) | **Your server** | Your environment | Anything else — internal APIs, services without MCP servers, anything needing a secret |

## Prebuilt agent toolset

Eight tools, enabled all at once:

```yaml
tools:
  - type: agent_toolset_20260401
    default_config:
      enabled: true
```

| Tool | What it does |
|---|---|
| `bash` | Run shell commands in the container |
| `read` | Read text/images/PDFs/notebooks from the filesystem |
| `write` | Write files |
| `edit` | String replacement in files |
| `glob` | Pattern-match file paths |
| `grep` | Regex search across files |
| `web_fetch` | Fetch a URL |
| `web_search` | Search the web |

Disable specific tools with per-tool `configs`:

```yaml
tools:
  - type: agent_toolset_20260401
    default_config:
      enabled: true
    configs:
      - name: bash
        enabled: false
```

Or invert: turn the default off, opt in to specific tools:

```yaml
tools:
  - type: agent_toolset_20260401
    default_config:
      enabled: false
    configs:
      - {name: read, enabled: true}
      - {name: glob, enabled: true}
      - {name: grep, enabled: true}
```

## MCP toolsets

Two-step wiring:

1. **Declare the server** on the agent (no auth):
   ```yaml
   mcp_servers:
     - type: url
       name: github
       url: https://api.githubcopilot.com/mcp/

   tools:
     - type: agent_toolset_20260401
     - type: mcp_toolset
       mcp_server_name: github
   ```

2. **Create a vault** with OAuth credentials and **attach to sessions**:
   ```python
   vault = client.beta.vaults.create(name="github-creds")
   client.beta.vaults.credentials.create(
       vault_id=vault.id,
       display_name="GitHub OAuth",
       auth={
           "type": "mcp_oauth",
           "mcp_server_url": "https://api.githubcopilot.com/mcp/",
           "access_token": "<initial token>",
           "refresh": {
               "refresh_token": "<initial refresh token>",
               "client_id": "<your OAuth client_id>",
               "token_endpoint": "https://github.com/login/oauth/access_token",
               "token_endpoint_auth": {"type": "client_secret_post", "client_secret": "<secret>"},
           },
       },
   )

   # On every sessions.create() that uses this MCP:
   session = client.beta.sessions.create(
       agent=AGENT_ID,
       environment_id=ENV_ID,
       vault_ids=[vault.id],
   )
   ```

Anthropic refreshes the token before expiry using the `refresh` block. The credential never enters the container — Anthropic injects it after the request leaves the sandbox.

**MCP auth tokens are not the service's REST API keys.** A Notion `ntn_` integration token is for Notion's REST API; the Notion MCP server expects an OAuth bearer. Different auth systems.

### Composio — broad integration coverage via one hosted MCP

[Composio](https://composio.dev) is the highest-leverage MCP option when your agent needs many integrations (Gmail, GitHub, Slack, Notion, Calendar, Linear, Stripe, etc.). One MCP endpoint exposes ~1000 toolkits; Composio handles auth (per-user OAuth flows, token refresh) and tool discovery.

For Managed Agents, the wiring is two-step:

1. **Declare the MCP server** on the agent (no auth here):

   ```yaml
   # agent.yaml
   mcp_servers:
     - type: url
       name: composio
       url: https://mcp.composio.dev/mcp/<session-or-tenant-path>
   tools:
     - {type: agent_toolset_20260401}
     - {type: mcp_toolset, mcp_server_name: composio}
   ```

2. **Create a vault** with the Composio API key as the credential, and attach via `vault_ids` on session create.

The per-user `user_id` model that Composio's Python SDK uses (`Composio().create(user_id=...)`) maps to a stable URL or tenant identifier in the MCP path — see the [Composio MCP docs](https://docs.composio.dev) for the current shape, since the pattern has shifted across versions. For multi-tenant Managed Agents, this is the cleanest way to keep OAuth tokens per end-user without writing a credential manager yourself.

When **not** to reach for Composio in Managed Agents: when you only need 1-2 integrations (the per-service MCP servers — like the GitHub MCP — give you tighter control), or when you need bespoke business logic on top of the integration (use a custom tool to compose Composio's tools host-side).

## Custom tools — for everything else

Custom tools are how you give the agent capabilities backed by your own code. They run on **your server**, not in Anthropic's container. The flow:

1. Agent emits `agent.custom_tool_use` event with input JSON.
2. Session goes idle waiting for you.
3. Your orchestrator (the process consuming the SSE stream) handles it.
4. You respond with `user.custom_tool_result`.
5. Session resumes.

Two-step authoring:

```yaml
# 1. Schema in agent.yaml
tools:
  - type: custom
    name: lookup_user
    description: Look up a user by ID — verified profile + recent activity.
    input_schema:
      type: object
      properties:
        user_id: {type: string}
      required: [user_id]
```

```python
# 2. Handler in your orchestrator (host-side)
def lookup_user(args: dict) -> str:
    user_id = args["user_id"]
    return await crm.get_user(user_id)  # uses YOUR API keys, in YOUR env

# In the orchestrator loop:
if event.type == "agent.custom_tool_use" and event.name == "lookup_user":
    result = lookup_user(event.input)
    client.beta.sessions.events.send(
        session_id=session.id,
        events=[{"type": "user.custom_tool_result",
                 "custom_tool_use_id": event.id,
                 "content": [{"type": "text", "text": str(result)}]}],
    )
```

The nuvel scaffold's `tools/__init__.py` already wires this — add a handler, register it in `_HANDLERS`, run `setup.py` to apply the new schema.

## When to use which — the decision tree

```
Does the capability already exist as a hosted MCP server?
├── Yes → MCP toolset + vault.
└── No → Does the work need a secret (API key, DB cred, internal-only network)?
    ├── Yes → Custom tool (host-side).
    └── No → Does the work map to bash / file / web operations?
        ├── Yes → Prebuilt agent toolset is sufficient.
        └── No → Custom tool (host-side) — even without secrets, this is where bespoke logic goes.
```

**Anti-pattern:** trying to make a third-party API work via `bash` + `curl` because *"it's just an HTTP call"*. The container has no env vars, so there's nowhere to put the API key. Either the service has an MCP server (use it) or you write a custom tool (do this).

## Permission policies

For prebuilt and MCP tools, you can require approval before each call:

```yaml
tools:
  - type: agent_toolset_20260401
    default_config:
      enabled: true
    configs:
      - name: bash
        permission_policy: {type: always_ask}
```

When the policy fires, the agent emits `agent.tool_use` with `evaluated_permission: "ask"` and goes idle. Reply with `user.tool_confirmation`:

```python
client.beta.sessions.events.send(
    session_id=session.id,
    events=[{"type": "user.tool_confirmation",
             "tool_use_id": event.id,
             "result": "allow"}],  # or "deny" with optional "deny_message"
)
```

Use sparingly — every prompt is a latency penalty. Reserve for genuinely irreversible actions.

## Quick reference

| Task | API |
|---|---|
| Enable all prebuilt tools | `tools: [{type: agent_toolset_20260401}]` |
| Disable one prebuilt tool | `default_config: {enabled: true}` + `configs: [{name: bash, enabled: false}]` |
| Wire an MCP server | `mcp_servers: [{type: url, name, url}]` + `tools: [{type: mcp_toolset, mcp_server_name}]` + `vault_ids` on session |
| Declare a custom tool | `tools: [{type: custom, name, description, input_schema}]` + handler + registry mapping |
| Require approval | `permission_policy: {type: always_ask}` on a prebuilt or MCP tool |
