# Reach an agent on Telegram

`--with-telegram` adds a Telegram Bot API inbound webhook and direct outbound replies to a generated ADK agent. Two env vars + one `setWebhook` curl.

ADK only.

## Scaffold

```bash
nuvel agent create my-agent --framework adk --with-telegram
```

The overlay drops `my_agent/gateways/telegram.py` and mounts it at `POST /gateways/telegram` on the agent server. Adds `httpx>=0.27.0` to `requirements.txt`.

## Create a bot

1. DM [@BotFather](https://t.me/BotFather) on Telegram.
2. `/newbot` → pick a name and a username (`yourbot_bot`).
3. BotFather replies with the bot **token**. Treat it like a password.

## Configure

In `.env`:

```
TELEGRAM_BOT_TOKEN=123456:AAAA-yourBotFatherToken
TELEGRAM_WEBHOOK_SECRET=<long random string>

# Optional: bot username (without @) for group-mention detection.
# TELEGRAM_BOT_USERNAME=yourbot_bot
```

`TELEGRAM_WEBHOOK_SECRET` should be a strong random string. Telegram echoes it on every delivery in the `X-Telegram-Bot-Api-Secret-Token` header; the handler verifies via constant-time comparison.

## Register the webhook

After deploying so `https://<your-host>/gateways/telegram` is reachable:

```bash
curl -F "url=https://<your-host>/gateways/telegram" \
     -F "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
     "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook"
```

Expected response:

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

## Verify

DM the bot in Telegram. You should see a typing indicator within a second, followed by the agent's reply.

In a group, the bot only replies when:

- The message starts with `/` followed by the bot's username (`/help@yourbot_bot`), **or**
- The message contains an `@yourbot_bot` mention, **or**
- The message is a reply to a bot-authored message.

This is the standard well-behaved-bot convention. To trigger this detection, set `TELEGRAM_BOT_USERNAME` in `.env`.

## Session model

- **Private chat (DM)** → one continuous session per user (`telegram:dm:{from_id}`).
- **Group** → one session per group (`telegram:group:{chat_id}`).
- **Forum topic** (in supergroups with topics enabled) → one session per topic (`telegram:group:{chat_id}:{message_thread_id}`).

## Local development

```bash
ngrok http 8000
```

Then:

```bash
curl -F "url=https://<ngrok-host>/gateways/telegram" \
     -F "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
     "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook"
```

Re-run when the ngrok URL changes. To stop receiving updates while debugging:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/deleteWebhook"
```

## Troubleshooting

- **`401 Unauthorized` from `/gateways/telegram`** → `TELEGRAM_WEBHOOK_SECRET` doesn't match what you passed to `setWebhook`. Re-run `setWebhook` with the right value.
- **No reply in groups** → set `TELEGRAM_BOT_USERNAME`. Without it, mention detection silently fails.
- **`getWebhookInfo`** is your friend for debugging:

  ```bash
  curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
  ```

  Look at `pending_update_count`, `last_error_date`, and `last_error_message`.

## Outbound

Replies go directly to Telegram's Bot API via `httpx`. The handler:

- Sends `sendChatAction: typing` immediately, then again every 4 seconds while the agent runs.
- Posts the final reply with `reply_to_message_id` (groups only) and `message_thread_id` (forum topics only).
- One attempt, no retry queue. Failures are logged, not surfaced to the user.
