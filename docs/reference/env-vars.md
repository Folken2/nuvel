# Environment variables

Every env var a scaffolded agent reads, grouped by feature. The full set surfaces in `.env.example` after scaffolding — this page is the one-stop reference.

## Core (always present)

| Var | Required | Default | Description |
|---|---|---|---|
| `DEV_MODE` | no | `false` | `true` = in-memory sessions; `false` = production (requires `SESSION_SERVICE_URI`). |
| `PORT` | no | `8000` | Port the FastAPI agent server binds to. Railway/most platforms set this automatically. |
| `AGENTS_DIR` | no | `.` | Directory ADK searches for agent definitions. |
| `LOG_FORMAT` | no | `text` | `text` or `json` for structured logs. |
| `STREAMING_ENABLED` | no | `false` | `true` = enable WebSocket streaming mode (uses Gemini live model). |

## Database (production)

| Var | Required | Description |
|---|---|---|
| `SESSION_SERVICE_URI` | yes when `DEV_MODE=false` | Postgres connection URI. Both `postgresql://` and `postgresql+asyncpg://` schemes work; the agent normalizes them. |

## LLM (ADK)

| Var | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | yes | OpenRouter API key. Get one at [openrouter.ai/keys](https://openrouter.ai/keys). |
| `FAST_MODEL` | no | Override the default fast model (e.g. `openrouter/moonshotai/kimi-k2.5`). |
| `REASONING_MODEL` | no | Override the default reasoning model (e.g. `openrouter/google/gemini-3-pro-preview`). |
| `OPENROUTER_REFERER` | no | Custom OpenRouter attribution header. Defaults to a per-agent value. |

## Auth

| Var | Required | Description |
|---|---|---|
| `API_KEY` | recommended in prod | Bearer token (or `X-API-Key` header) required on all endpoints except `/health`, `/favicon.ico`, and `/gateways/*`. If unset, the server warns and runs unauthenticated. |
| `DOCS_ENABLED` | no | If `true`, keeps `/docs`, `/openapi.json`, `/redoc` reachable when `API_KEY` is set. Default is to disable docs in production. |

## Composio (`--with-composio`)

| Var | Required | Description |
|---|---|---|
| `COMPOSIO_API_KEY` | yes | Composio API key. Get one at [composio.dev](https://composio.dev). |
| `COMPOSIO_USER_ID` | no | User identity scoped to the Composio session. Default `"default"`. |

## Slack channel (`--with-slack`)

| Var | Required | Description |
|---|---|---|
| `COMPOSIO_API_KEY` | yes | (Inherited from `--with-composio`, which `--with-slack` auto-enables.) |
| `COMPOSIO_WEBHOOK_SECRET` | yes | Long random string. Composio echoes it back as the `?secret=` query parameter on every trigger delivery; the handler verifies via constant-time comparison. |
| `SLACK_BOT_USER_ID` | no | The bot's Slack user ID (e.g. `U0BOT...`). Required for `@`-mention detection in channels. Without it, channel-mode `mention` silently drops every channel message. |
| `SLACK_CHANNEL_TRIGGER_MODE` | no | `mention` (default) — only invoke on `@`-mention; or `all` — invoke on every channel message. |

## Telegram channel (`--with-telegram`)

| Var | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token from `@BotFather`. |
| `TELEGRAM_WEBHOOK_SECRET` | yes | Long random string. Telegram echoes it back in `X-Telegram-Bot-Api-Secret-Token`; the handler verifies via constant-time comparison. |
| `TELEGRAM_BOT_USERNAME` | no | Bot username (without `@`). Required for `@`-mention detection in groups. |

## Teams sidecar (`--with-teams`)

The Teams bridge runs as a separate process (`python -m {agent_package}.gateways.teams_bridge`).

### Mode selection

The bridge auto-detects between two modes based on the presence of these three SDK-mode vars:

| Var | Mode | Description |
|---|---|---|
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID` | SDK | Bot Framework App ID. |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET` | SDK | Bot Framework App Password. |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID` | SDK | Azure tenant ID. |

If all three are set → SDK mode (production, full JWT validation).
If any is missing → anonymous mode (Agents Playground / local dev only; **not** for public exposure).

### Bridge → agent connection

| Var | Default | Description |
|---|---|---|
| `AGENT_BASE_URL` | `http://127.0.0.1:8000` | Where the agent server is reachable from the bridge. |
| `AGENT_APP_NAME` | scaffolded agent name | ADK app name to send to the agent server. |
| `API_KEY` | — | Bearer token for the agent server (same value as the agent's `API_KEY`). |
| `AGENT_TIMEOUT_SECONDS` | `120` | HTTP timeout for bridge → agent calls. |

### Bridge runtime

| Var | Default | Description |
|---|---|---|
| `TEAMS_BRIDGE_PORT` | `3978` | Port the bridge listens on. |
| `TEAMS_BRIDGE_HOST` | `localhost` | Bind host. |
| `TEAMS_ENABLE_INTERMEDIATE_MESSAGES` | `true` | Send "Working on it…" while the agent runs. |
| `TEAMS_PROGRESS_TEXTS` | (4 default phrases) | Pipe-delimited list. |
| `TEAMS_PROGRESS_MIN_DELAY_MS` | `350` | Min delay between progress messages. |

### Attachment ingestion

| Var | Default | Description |
|---|---|---|
| `TEAMS_ENABLE_ATTACHMENT_CONTEXT` | `true` | Surface attachment metadata to the agent. |
| `TEAMS_MAX_ATTACHMENT_COUNT` | `5` | Max attachments per message. |
| `TEAMS_ENABLE_ATTACHMENT_DOWNLOAD` | `true` | Download and extract text from attachments (PDF, text, JSON, CSV). |
| `TEAMS_MAX_ATTACHMENT_BYTES` | `500000` | Max bytes downloaded per attachment. |
| `TEAMS_MAX_INLINE_B64_CHARS` | `1500000` | Max base64-encoded chars when forwarding raw inline attachments. |
| `TEAMS_FORWARD_RAW_ATTACHMENTS` | `false` | If `true`, forward raw `inline_data` / `file_data` parts to ADK instead of just text-extracted context. |
