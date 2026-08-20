# Wire 1000+ tools with Composio

The `--with-composio` overlay attaches the [Composio Tool Router](https://composio.dev) to a generated ADK agent. The Tool Router is a single hosted MCP endpoint that exposes ~1000 third-party toolkits (Gmail, GitHub, Slack, Notion, Calendar, Drive, Linear, Stripe, …) with one wire-up.

Available on **ADK only** today. Other backends accept the flag but error out.

## Scaffold with Composio

```bash
nuvel agent create my-agent --framework adk --with-composio
```

The overlay drops:

- `my_agent/composio_mcp.py` — builds the Composio MCP toolset.
- `tools/__init__.py` — extends to include the toolset when configured.
- `.env.example` — adds `COMPOSIO_API_KEY` and an optional `COMPOSIO_USER_ID`.
- `requirements.txt` — pins `composio>=1.0.0rc10`.

## Configure

In your agent's `.env`:

```
COMPOSIO_API_KEY=ck_live_...
# Optional: scope the Composio user identity. Default is "default".
# COMPOSIO_USER_ID=alice@example.com
```

Get the API key at [composio.dev](https://composio.dev) (free tier covers most prototyping).

## Connect specific apps

In the Composio dashboard, create connections for the apps the agent should use (Gmail, GitHub, etc.). Each connection's auth flow happens once per `COMPOSIO_USER_ID`. The Tool Router exposes only connected toolkits to the agent.

## What the agent sees

Once configured, the agent has access to a `composio_*` toolset that calls into Composio's Tool Router via MCP. The bundled `composio-tool-router` skill teaches the agent how to discover and call tools from this toolset.

```bash
nuvel skills search composio --framework adk
```

## Combining with messaging gateways

`--with-slack` automatically enables `--with-composio` because the Slack channel uses Composio's Slackbot toolkit for both inbound triggers and outbound message posting. See [Slack](add-slack.md) for the channel-specific setup.

## When *not* to use Composio

- Your agent only needs one or two tools — write them directly as Python functions in `my_agent/tools/`. Lower latency, no third-party dependency.
- You're on the Claude Agent SDK or Anthropic Managed Agents backend — Composio integration isn't supported there yet.
- You need full control over OAuth scopes — Composio's managed apps lock you into their scope set.
