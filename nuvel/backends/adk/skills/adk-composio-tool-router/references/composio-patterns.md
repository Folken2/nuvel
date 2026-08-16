# Composio deeper patterns

Supporting detail for the "Common patterns" pointer in `SKILL.md`. Each section below
expands on something the main skill body already establishes.

## Multi-tenant request routing

Don't reach for the `COMPOSIO_USER_ID` env var in a multi-tenant deployment. That
variable bakes a single identity into process startup, so every request through that
process would share one set of OAuth connections — exactly the cross-user leakage the
multi-tenant section warns about.

Instead, build the toolset per request from the authenticated end-user's ID:

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

This is the "per-request `user_id`" pattern from the main skill. `Composio().create()`
is an alias for `ToolRouter.create` (`composio/sdk.py:172`), and it **creates a new
server-side tool-router session**, returning a fresh `session_id`
(`composio/core/models/tool_router.py:786` — `self._client.tool_router.session.create(...)`).
It is *not* a lookup of an existing session: retrieval is a different method,
`Composio().use(session_id)` → `tool_router.session.retrieve` (`tool_router.py:855`).

What it is not is a fresh OAuth handshake — the connections already exist from the
one-time dashboard step, so session creation is fast and doing it per incoming request
is normally fine. But because each call mints a new session rather than reusing one,
this *is* a place where caching matters if you're creating sessions at high rate:
hold the returned `session_id` for the life of a conversation and re-enter it with
`use(session_id)`, rather than calling `create()` on every turn.

## Service-account vs end-user-account models

The trade-off is exactly the one-line summary in the main skill: a shared
service-account `user_id` is simpler to operate — one connection to manage, no
per-user provisioning — but gives no per-tenant isolation. Every request through that
identity sees the same connected toolkits and the same OAuth grants.

Use the service-account model only when the agent is acting on its own data (its own
Gmail inbox, its own GitHub org) rather than on behalf of individual end-users. As soon
as the agent needs to act *as* different users — sending email from each user's own
account, filing issues under each user's own GitHub identity — the service-account
model is the wrong shape and you need per-request `user_id` routing instead.

## Composio with --persona agents

A persona agent's self-authored skills can include instructions for managing its own
Composio connections — connecting new toolkits, or reconfiguring which ones it uses,
as part of its normal skill-writing behavior.

That combination is worth pausing on: a self-rewriting agent that also holds live
OAuth connections widens the blast radius of anything that goes wrong. A bug or bad
self-authored skill isn't just misbehaving code anymore — it's misbehaving code with
standing access to whatever third-party accounts are connected under its `user_id`
(Gmail, GitHub, Slack, etc.). Pair a `--persona` agent that also uses Composio with
`adk-long-horizon-guardrails`, so the checks that would normally catch a bad
self-modification are in place before the agent can act on connected accounts.

## Filtering toolkits per agent

There are two real filtering mechanisms, at two different layers. Reach for either or
both — you do **not** need extra `user_id`s or extra Composio accounts to give two
agents different tool access.

**1. Filter at the Composio session (server side).** `ToolRouter.create` — which
`Composio().create()` aliases — takes `toolkits=`, `tools=` and `tags=` keyword
arguments that scope what the session exposes at all
(`composio/core/models/tool_router.py:441-461`):

```python
session = Composio().create(
    user_id=end_user_id,
    toolkits=["gmail", "slack"],                      # or {"enable": [...]} / {"disable": [...]}
    tools={
        "gmail": ["GMAIL_SEND_EMAIL", "GMAIL_SEARCH"],  # list = enable-only
        "slack": {"disable": ["SLACK_DELETE_MESSAGE"]}, # blacklist
        "linear": {"tags": ["readOnlyHint"]},           # filter by MCP tag
    },
    tags=["readOnlyHint", "idempotentHint"],            # global tag filter
)
```

Per the parameter docs, `toolkits` accepts a list of slugs or an `{"enable": [...]}` /
`{"disable": [...]}` dict; `tools` maps a toolkit slug to a list of tool slugs
(shorthand for enable), or a dict with `enable` / `disable` / `tags`; `tags` accepts
the MCP tag literals `readOnlyHint`, `destructiveHint`, `idempotentHint`,
`openWorldHint`, and toolkit-level tags override the global setting. This is the
mechanism to use for "this agent may send mail but not delete it."

**2. Filter at the `McpToolset` (client side).** ADK's `McpToolset.__init__` takes
`tool_filter: Optional[Union[ToolPredicate, List[str]]] = None` — "a list of tool names
to include, or a `ToolPredicate` function for custom filtering logic"
(`google/adk/tools/mcp_tool/mcp_toolset.py:106`, documented at `:134-136`):

```python
McpToolset(
    connection_params=StreamableHTTPConnectionParams(url=..., headers=...),
    tool_filter=["GMAIL_SEND_EMAIL", "GMAIL_SEARCH"],
)
```

This is how two agents can share one Composio session and still see different tool
lists — useful when the session is built once at startup but several agents consume it.

You can also still filter at connection time in the dashboard, by only connecting the
toolkits an agent should ever reach. And per-`user_id` scoping remains the right tool
for **credential isolation** — keeping user A's OAuth grants out of user B's session —
it just isn't the mechanism for narrowing which tools an agent may call.

## Debugging "tool not found"

Work through this checklist in order:

1. **Is `COMPOSIO_API_KEY` set?** If it's unset, the toolset gracefully no-ops — the
   agent starts fine with only its local tools, and every Composio tool name will
   look like "not found" because the `McpToolset` was never created. It's not
   completely silent: an INFO line is logged
   (`"COMPOSIO_API_KEY not set — Composio Tool Router disabled."`,
   `<package>/tools/composio_mcp.py:26`), so check the startup logs at INFO level.
2. **Was the toolkit connected for *this* `user_id`?** Connections are scoped per
   user; a toolkit connected under `user_id="alice"` is invisible to a session created
   with `user_id="bob"` or `user_id="default"`. Check the Composio dashboard for which
   identity actually has the connection.
3. **Does the tool name match `<TOOLKIT>_<ACTION>`?** Composio prefixes every tool by
   toolkit (e.g. `GMAIL_SEND_EMAIL`, not `send_email` or `SendEmail`). A near-miss name
   guessed from memory won't resolve.
4. **Did the OAuth flow complete?** A toolkit that was started in the dashboard but
   never finished authorizing won't expose its tools, even though it may show up as
   "connecting" rather than a clean failure.
