# Reach an agent on Slack

`--with-slack` adds a Slack inbound webhook and outbound replies to a generated ADK agent, using Composio's Slackbot toolkit end-to-end. Setup is two env vars + one CLI command per trigger.

ADK only. Auto-enables `--with-composio`.

## Scaffold

```bash
nuvel agent create my-agent --framework adk --with-slack
```

You'll see:

```
[nuvel] --with-slack uses Composio Slackbot — enabling --with-composio.
Channels: slack
Bundles: composio
```

The overlay drops `my_agent/gateways/slack.py` and wires it into `run_adk.py`.

## Configure

In `.env`:

```
COMPOSIO_API_KEY=ck_live_...
COMPOSIO_WEBHOOK_SECRET=<long random string>

# Optional: enables @-mention detection for channel messages.
# SLACK_BOT_USER_ID=U0BOT...

# Optional: 'all' = invoke on every channel message; 'mention' (default) = only on @-mention.
# SLACK_CHANNEL_TRIGGER_MODE=mention
```

Generate a strong `COMPOSIO_WEBHOOK_SECRET` (32+ random characters). Composio echoes it back as a query parameter on every delivery; the handler verifies via constant-time comparison.

## Connect Slack

In the Composio dashboard:

1. **Apps → Slackbot → Connect.** Walk through the Slack OAuth flow against your workspace. Composio handles the bot install, scopes, and token storage.
2. **Note the bot's user ID** (looks like `U0BOTABC123`). Set `SLACK_BOT_USER_ID` in `.env` so the gateway can detect @-mentions in channels.

## Subscribe triggers

After deploying the agent (so `https://<your-host>/gateways/slack/composio` is reachable), use the Composio CLI to register at least the two essential triggers:

```bash
composio trigger create SLACKBOT_DIRECT_MESSAGE_RECEIVED \
    --webhook "https://<your-host>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET"

composio trigger create SLACKBOT_CHANNEL_MESSAGE_RECEIVED \
    --webhook "https://<your-host>/gateways/slack/composio?secret=$COMPOSIO_WEBHOOK_SECRET"
```

Optional triggers (the gateway logs but doesn't act on them by default):

- `SLACKBOT_MESSAGE_REACTION_ADDED`
- `SLACKBOT_CHANNEL_CREATED`

## Verify

DM the bot in Slack. The agent replies inline.

In a channel where the bot is a member, `@your-bot what's up?` opens a thread with the bot's reply. Subsequent messages in that thread share session state.

## Session model

- **DM** → one continuous session per user (`slack:dm:{team_id}:{channel_D}`).
- **Channel @-mention** → one session per thread (`slack:thread:{team_id}:{channel}:{thread_ts}`). A fresh `@bot` mention in the same channel starts a new thread.

The agent's memory persists across DMs and across thread conversations; it does not bleed between threads in the same channel.

## Local development

For local testing, expose your dev server with [ngrok](https://ngrok.com):

```bash
ngrok http 8000
```

Use the ngrok URL when running `composio trigger create`. Re-run the create command whenever the ngrok URL changes.

## Troubleshooting

- **401 from `/gateways/slack/composio`** → `COMPOSIO_WEBHOOK_SECRET` mismatch between `.env` and the trigger's `?secret=` query param.
- **`COMPOSIO_API_KEY is required when the Slack gateway is active`** at startup → set `COMPOSIO_API_KEY` in `.env`. The gateway intentionally fails loud at startup rather than silently at first webhook.
- **Bot doesn't respond to channel mentions** → check `SLACK_BOT_USER_ID` is set. Without it, the default `mention` mode can't detect @-mentions and silently drops channel messages. Workaround: set `SLACK_CHANNEL_TRIGGER_MODE=all` to invoke on every channel message (noisy).

## Outbound

Replies go through Composio's `SLACKBOT_SEND_MESSAGE`. The agent can also call Slack tools directly as part of its workflow (the Composio Tool Router exposes the full Slackbot toolkit — search messages, look up users, post to other channels, etc.).
