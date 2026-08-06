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
is a lightweight session lookup, not a fresh OAuth handshake — the connections already
exist from the one-time dashboard step — so creating one per incoming request is fast
enough to do on the hot path. There's no need to cache or pool sessions to make this
viable; the cost you're paying is a network round trip to Composio, not a slow
authentication flow.

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

The MCP transport itself doesn't support per-tool gating — once a toolkit is connected
under a `user_id`, every tool in it is visible to any agent using that session. There's
no filter you can apply at the `McpToolset` layer to hide `GMAIL_SEND_EMAIL` while
keeping `GMAIL_READ_EMAIL`.

So filtering happens one layer up, at connection time: only connect the toolkits an
agent should be able to reach, scoped to the `user_id` that agent runs as. If two
agents need different toolkit access, give them different `user_id`s (or different
Composio accounts) rather than trying to filter a shared session's tool list.

## Debugging "tool not found"

Work through this checklist in order:

1. **Is `COMPOSIO_API_KEY` set?** If it's unset, the toolset no-ops silently — the
   agent starts fine with only its local tools, and every Composio tool name will
   look like "not found" because the `McpToolset` was never created.
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
