# Messaging Gateways for Generated Agents — Design Spec

> **Date:** 2026-05-09
> **Status:** Proposed
> **Goal:** Make a nuvel-scaffolded ADK agent reachable from Slack, Telegram, and Microsoft Teams with a single CLI flag and `.env`-only configuration.

---

## Context

Today, a nuvel-scaffolded agent only exposes the ADK FastAPI surface (`/run`, `/run_sse`, etc.) plus a small custom layer in `run_adk.py` (`/health`, `/debug-info`). To put the agent in front of real humans on a chat platform, the agent owner has to write the inbound webhook handlers, signature verification, session mapping, and outbound API calls themselves. This spec adds a built-in path so the answer is `--with-slack --with-telegram --with-teams`.

A working v1 of an MS Teams ↔ ADK bridge already exists in production (`reference/teams-v1/data-analysis-agent/run_m365_bridge.py`). It uses the Microsoft 365 Agents SDK (`microsoft-agents-*`), supports both an SDK-mode (production, JWT-validated) and an anonymous mode (Agents Playground / local dev), and proxies to the ADK server over its REST API. We port it forward.

**Non-goals (explicit):**

- Streaming token-by-token replies into chat. v1 is final-only (with a "thinking…" interim message where the platform allows it).
- True message editing beyond what each platform's outbound API does naturally.
- Cross-platform user identity unification (Slack U012 ≠ Teams aadObjectId by design in v1).
- A custom (non-Composio) Slack path.
- Long-polling for Telegram.
- Channels for `claude-agent-sdk` or `anthropic-managed-agents` backends.
- OAuth wizards triggered by the nuvel CLI itself.
- Built-in retry queues for outbound failures.
- Cross-platform identity, multi-workspace fan-out, fancy Block Kit / Adaptive Cards composition.

These are clean v1.x extensions; the gateway abstractions defined below do not preclude any of them.

---

## Design

### 1. Architecture overview

A nuvel-scaffolded ADK agent stays a single FastAPI server. The per-channel CLI flags (`--with-slack`, `--with-telegram`, `--with-teams`) activate **gateway overlays** that drop a `gateways/` package into the agent and wire two surfaces:

- **Slack (Composio Slackbot) and Telegram** → FastAPI `APIRouter`s mounted on the existing `run_adk.py` app under `/gateways/<platform>`. Same process, same port, in-process ADK `Runner` invocation, no HTTP hop.
- **MS Teams** → standalone aiohttp sidecar process (`teams_bridge.py`), direct port of the v1, talks to the agent over its existing REST API at `/run`. Separate process, default port `3978`.

The asymmetry is driven by the Microsoft 365 Agents SDK: production-mode JWT validation goes through `microsoft_agents.hosting.aiohttp.CloudAdapter`, which expects to own its own aiohttp `Application`. Bridging an aiohttp adapter inside a FastAPI app is fragile, and reimplementing the JWT validation by hand is error-prone. The v1 already chose the sidecar architecture and it is the production-tested path; we adopt it.

Agents scaffolded **without** any `--with-*` channel flag produce byte-identical output to today.

### 2. Backend scope (v1)

V1 supports `--framework adk` only. For `--framework claude-agent-sdk` and `--framework anthropic-managed-agents`, the channel flags are accepted at the CLI level but the scaffolder exits with:

```
Error: --with-<channel> is not yet supported for the <framework> backend.
       Open an issue if you need it: https://github.com/.../issues
```

Channel handlers are reusable across backends in principle (only the agent-invocation seam differs), so v1.x can extend by introducing a small `Invoker` abstraction once a second backend is genuinely needed.

### 3. CLI surface

```
nuvel new my-agent --framework adk --with-slack --with-telegram --with-teams
```

- Each channel has its own boolean flag: `--with-slack`, `--with-telegram`, `--with-teams`. This matches the existing flag style used by `--with-composio` and `--persona`.
- All three default to off. Omitting all flags = no gateways = byte-identical to today's output.
- `--with-slack` implies `--with-composio`. If the user passes `--with-slack` without `--with-composio`, the scaffolder enables it automatically and prints: `[nuvel] --with-slack uses Composio Slackbot — enabling --with-composio.`
- The success summary lists active channels:
  ```
  Bundles: composio
  Channels: slack, telegram, teams
  ```
  (only the line with non-empty content is printed; backwards-compatible with today's `Bundles:` line)

### 4. Scaffold mechanism (overlays)

Each channel is implemented as an **overlay** under `nuvel/backends/adk/templates_overlays/`, mirroring the existing `persona/` and `composio/` overlays.

```
nuvel/backends/adk/templates_overlays/
├── persona/                    # existing
├── composio/                   # existing
├── gateway-base/               # NEW — stamped if any channel is selected
│   └── {{agent_package}}/
│       └── gateways/
│           ├── __init__.py
│           └── _common.py
├── gateway-slack/              # NEW
│   └── {{agent_package}}/gateways/slack.py
├── gateway-telegram/           # NEW
│   └── {{agent_package}}/gateways/telegram.py
└── gateway-teams/              # NEW
    └── {{agent_package}}/gateways/teams_bridge.py
```

The overlay loop in `nuvel/backends/adk/scaffold.py` is extended to apply gateway overlays after `composio/`. Order: `gateway-base` first, then each selected channel in canonical order (`slack`, `telegram`, `teams`).

`run_adk.py` gets two new placeholder substitutions:

- `{{gateway_imports}}` — populated with channel-specific imports, e.g.
  ```python
  from {{agent_package}}.gateways import slack as gw_slack
  from {{agent_package}}.gateways import telegram as gw_telegram
  ```
- `{{gateway_mounts}}` — populated with the corresponding mount calls, e.g.
  ```python
  app.include_router(gw_slack.router)
  app.include_router(gw_telegram.router)
  ```

Both placeholders expand to empty strings when no channels are selected; the resulting `run_adk.py` is then byte-identical to today's output.

`requirements.txt` gains a `{{gateway_requirements}}` placeholder, populated as needed:

- Slack: nothing extra (Composio dependency comes from `--with-composio`).
- Telegram: nothing extra (uses already-present `httpx`).
- Teams: appends
  ```
  microsoft-agents-hosting-aiohttp
  microsoft-agents-authentication-msal
  aiohttp
  pypdf
  ```

`.env.example` gains a `{{gateway_env_block}}` placeholder, populated with one block per active channel (see §10).

### 5. The shared `_common` module

`{{agent_package}}/gateways/_common.py` provides three things shared by Slack and Telegram (Teams keeps its own logic, ported from v1):

```python
def session_key(platform: str, payload: dict) -> tuple[str, str]:
    """Compose (user_id, session_id) per the hybrid policy in §6."""

async def ensure_session(
    session_service: BaseSessionService,
    app_name: str,
    user_id: str,
    session_id: str,
) -> None:
    """Idempotent session creation. Re-used by both in-process invokers."""

async def invoke_agent(
    runner: Runner,
    user_id: str,
    session_id: str,
    text: str,
    files: list[Part] | None = None,
) -> str:
    """Run the agent in-process; return the final text reply.
    Iterates runner.run_async events, collects text parts, returns the last
    non-empty assistant utterance (matches the v1's text-extraction rule)."""
```

The runner instance is constructed once at app startup (in `run_adk.py`'s `main()`) and stored on `app.state.runner`. Each gateway router pulls it from there — no module-level globals.

### 6. Session mapping (hybrid policy)

| Platform | `user_id` | `session_id` |
|---|---|---|
| Slack DM | `slack:{team_id}:{user}` | `slack:dm:{team_id}:{channel}` |
| Slack channel mention | `slack:{team_id}:{user}` | `slack:thread:{team_id}:{channel}:{thread_ts or message_ts}` |
| Telegram private chat | `telegram:{from.id}` | `telegram:dm:{from.id}` |
| Telegram group | `telegram:{from.id}` | `telegram:group:{chat.id}` (+ `:{message_thread_id}` if forum topic) |
| Teams | `teams:{from.aadObjectId or from.id}` | `m365-{conversation.id}` (verbatim from v1) |

Channel mentions in Slack/Telegram start a thread if one doesn't exist; subsequent replies in that thread share session state. DMs are one continuous session per user. This matches user expectations and avoids cross-conversation context bleed.

ADK's `app_name` stays the scaffolded agent name (no change). All session strings are stable across deploys when `SESSION_SERVICE_URI` points at the same Postgres — restarts don't break ongoing conversations.

### 7. Slack handler (`gateways/slack.py`)

**Endpoint:** `POST /gateways/slack/composio`.

**Verification:** shared secret in query string. The handler compares `request.query_params["secret"]` to `os.environ["COMPOSIO_WEBHOOK_SECRET"]` via `secrets.compare_digest`. Failure → 401, no detail in the response body. Secret-in-URL is the established pattern Composio's trigger configuration supports out of the box; if Composio adds payload-signing later, this verification step can be tightened in place.

**Payload shape:** Composio wraps the trigger payload in a known envelope:
```json
{ "trigger_slug": "SLACKBOT_DIRECT_MESSAGE_RECEIVED",
  "payload": { ... Slack event fields ... },
  "connected_account_id": "...",
  "trigger_id": "..." }
```

**Dispatch by `trigger_slug`:**

| Trigger | v1 behavior |
|---|---|
| `SLACKBOT_DIRECT_MESSAGE_RECEIVED` | Invoke agent. Reply in the DM channel. |
| `SLACKBOT_CHANNEL_MESSAGE_RECEIVED` | Invoke agent **only** if the message text contains a self-mention or the configured `SLACK_CHANNEL_TRIGGER_MODE=all`. Reply in the same thread (or start one). |
| `SLACKBOT_MESSAGE_REACTION_ADDED` | Log only by default. |
| `SLACKBOT_CHANNEL_CREATED` | Log only by default. |
| Unknown / future | Log at INFO, return 200, do not raise. |

**Loop prevention:** drop messages where `bot_id` is set or `is_bot_message` is true.

**Ack pattern:** return 200 immediately. The agent run is dispatched via `asyncio.create_task` and posts the reply when complete.

**Outbound:** call `SLACKBOT_SEND_MESSAGE` via the Composio Python SDK using the same `composio_client` instance the `--with-composio` overlay already configured. Reply target:

- DM trigger → `channel = payload.channel`.
- Channel trigger → `channel = payload.channel`, `thread_ts = payload.thread_ts or payload.ts`.

**Optional UX (default on, configurable):** before invoking the agent, post a "Thinking…" message via `SLACKBOT_SEND_MESSAGE` and capture its `ts`. After the run, edit it via `SLACKBOT_UPDATES_A_MESSAGE`. If the edit fails, fall back to a fresh post.

**Setup story (post-deploy, in README):**
1. In Composio dashboard, connect Slack to the workspace.
2. Set webhook URL: `https://<deployment>/gateways/slack/composio?secret=<COMPOSIO_WEBHOOK_SECRET>`.
3. Subscribe triggers: `composio trigger create SLACKBOT_DIRECT_MESSAGE_RECEIVED --webhook <URL>` (and the other slugs you want).

### 8. Telegram handler (`gateways/telegram.py`)

**Endpoint:** `POST /gateways/telegram`.

**Verification:** Telegram's `X-Telegram-Bot-Api-Secret-Token` header compared against `os.environ["TELEGRAM_WEBHOOK_SECRET"]` via `secrets.compare_digest`. Failure → 401.

**Filter:** v1 handles only `update["message"]` with non-empty `text`. Edited messages, polls, callback queries, inline queries, etc. → 200, no-op (logged at DEBUG). This keeps the v1 surface tight; richer handling is a v1.x extension.

**Group filter:** in groups, only invoke if the message starts with `/` followed by the bot's username, mentions the bot via `entities[].type == "mention"`, or is a reply to a bot-authored message. (Mirrors how every well-behaved Telegram bot works.)

**Ack pattern:** identical to Slack (return 200, dispatch task, post reply).

**Outbound:** direct `httpx` POST to `https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage`, with:

- `chat_id = update.message.chat.id`
- `reply_to_message_id = update.message.message_id` (groups only)
- `message_thread_id = update.message.message_thread_id` (forum topics only)
- `parse_mode = "MarkdownV2"` with proper escaping; fallback to plain text if escaping fails.

**Optional UX:** send `sendChatAction` with `action=typing` immediately, then the final reply. Telegram refreshes the typing indicator for ~5 seconds, so for long runs we re-send periodically (every 4s) until the agent returns. This is implemented via a small `_typing_keepalive(chat_id)` background task.

**Setup story (README):**
1. Create bot via @BotFather, copy token into `TELEGRAM_BOT_TOKEN`.
2. Invent a random `TELEGRAM_WEBHOOK_SECRET` and put it in `.env`.
3. Run:
   ```
   curl -F "url=https://<deployment>/gateways/telegram" \
        -F "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
        https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook
   ```
4. (Local dev) `ngrok http 8000` first, then point `setWebhook` at the ngrok URL.

### 9. Teams sidecar (`gateways/teams_bridge.py`)

Direct port of `reference/teams-v1/data-analysis-agent/run_m365_bridge.py`. The diff against the v1 is intentionally minimal — the v1 is a known-good production artifact, and the goal is to keep it that way.

**Adjustments from v1:**

- `{{agent_package}}` substitution applied at scaffold time.
- Env-var renames to nuvel conventions:
  - `DATA_AGENT_BASE_URL` → `AGENT_BASE_URL`
  - `DATA_AGENT_APP_NAME` → `AGENT_APP_NAME` (defaults to the scaffolded agent name)
  - `DATA_AGENT_API_KEY` → reuses the existing `API_KEY` env var (no separate value needed)
  - `DATA_AGENT_TIMEOUT_SECONDS` → `AGENT_TIMEOUT_SECONDS`
  - `M365_*` → `TEAMS_*` (e.g. `TEAMS_PROGRESS_TEXTS`, `TEAMS_ENABLE_INTERMEDIATE_MESSAGES`, `TEAMS_MAX_ATTACHMENT_BYTES`, `TEAMS_BRIDGE_PORT`).
- The bridge port defaults to `int(os.getenv("TEAMS_BRIDGE_PORT", "3978"))`.
- Identical dual-mode behavior: SDK mode when `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{CLIENTID,CLIENTSECRET,TENANTID}` are set; anonymous mode otherwise.
- Identical attachment ingestion (PDF text via `pypdf`, configurable size limits, optional raw-attachment forwarding).
- Identical progress-message mechanism (configurable list, configurable min delay).
- Module-runnable: `python -m {{agent_package}}.gateways.teams_bridge`.

**`Dockerfile` policy:** unchanged. `CMD ["python", "run_adk.py"]` keeps the agent server as the container's primary process. Operators who want Teams in production are expected to run the bridge as a second container / process / supervisor entry — documented in the README. Bundling both into one container with a shell-based supervisor is rejected for v1 (couples lifecycle, hides crashes).

**Setup story (README):**
1. Register the bot in Azure Bot Service / Teams Developer Portal.
2. Configure `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__*` for production, or skip for Agents Playground / local dev.
3. Run `python -m {{agent_package}}.gateways.teams_bridge` on port 3978 (or set `TEAMS_BRIDGE_PORT`).
4. Set the bot's messaging endpoint to `https://<bridge-host>:3978/api/messages`.

### 10. Environment variables (per channel, `.env`-only in v1)

| Channel | Required | Optional |
|---|---|---|
| **Slack** | `COMPOSIO_API_KEY` (already present from `--with-composio`), `COMPOSIO_WEBHOOK_SECRET` | `SLACK_CHANNEL_TRIGGER_MODE` (default: `mention`; alt: `all`), `SLACK_THINKING_MESSAGE` (default: `Thinking…`) |
| **Telegram** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | `TELEGRAM_PARSE_MODE` (default: `MarkdownV2`) |
| **Teams** | (anonymous mode) none / (SDK mode) `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`, `..._CLIENTSECRET`, `..._TENANTID` | `AGENT_BASE_URL` (default: `http://127.0.0.1:8000`), `AGENT_APP_NAME` (default: scaffolded name), `AGENT_TIMEOUT_SECONDS` (default: 120), `TEAMS_BRIDGE_PORT` (default: 3978), `TEAMS_ENABLE_INTERMEDIATE_MESSAGES`, `TEAMS_PROGRESS_TEXTS`, `TEAMS_PROGRESS_MIN_DELAY_MS`, `TEAMS_ENABLE_ATTACHMENT_CONTEXT`, `TEAMS_MAX_ATTACHMENT_COUNT`, `TEAMS_ENABLE_ATTACHMENT_DOWNLOAD`, `TEAMS_MAX_ATTACHMENT_BYTES`, `TEAMS_MAX_INLINE_B64_CHARS`, `TEAMS_FORWARD_RAW_ATTACHMENTS` |

Each overlay's `.env.example` block clearly states whether each var is required for that channel.

### 11. Webhook auth integration

The existing `APIKeyMiddleware.PUBLIC_PREFIXES` constant in `run_adk.py` adds `/gateways`:

```python
PUBLIC_PREFIXES = ("/health", "/favicon.ico", "/gateways")
```

This means the `API_KEY` middleware doesn't gate gateway endpoints. Each handler is responsible for its own verification (Composio secret, Telegram secret, Bot Framework JWT in the Teams sidecar).

The Teams sidecar runs in a separate process and doesn't go through `APIKeyMiddleware` at all. Its `/api/messages` endpoint validates inbound Bot Framework activities via the `microsoft-agents` SDK in SDK mode; in anonymous mode it accepts unauthenticated POSTs (Agents Playground only — explicitly documented as not for production exposure).

### 12. Error handling

- **Webhook signature failures** → 401 with body `{"error": "Unauthorized"}`. No detail leakage.
- **Malformed payloads** → 400 with body `{"error": "Bad request"}`. Full payload logged at DEBUG with the request id.
- **Agent run exceptions** → caught at gateway boundary. Reply to the platform: `"Sorry, something went wrong (request id: {rid})."`. Full traceback to logs at ERROR with the request id (`RequestIDMiddleware` already attaches one).
- **Outbound platform API failures** (Slack 429, Telegram timeout, Teams adapter error) → logged at WARNING with retry count. v1 = single attempt + log; no built-in retry queue.
- **Unknown Composio trigger slugs** → logged at INFO, 200, no-op.
- **Session-service unavailability** (Postgres down) → bubbles up as a 500; existing ADK error semantics apply. Gateways do not silently swallow.

### 13. Testing

**Unit tests** (`tests/test_gateways_*.py`):

- Per-channel handler with payload fixtures.
  - Verification: signed/correct request → 200; unsigned/wrong → 401.
  - Dispatch: known trigger → invokes agent (mocked Runner); unknown trigger → 200 no-op.
  - Loop prevention: `is_bot_message=true` → 200 no-op, no agent invocation.
- Session-key composition: parametrized table mapping payload fixtures → expected `(user_id, session_id)`.
- Outbound formatting: agent reply + session metadata → expected platform-specific outbound payload (snapshot tests).

**Scaffold tests:** add to `tests/test_end_to_end.py` style suite:

- `nuvel new --with-slack` → verify `gateways/slack.py` and `gateways/_common.py` land, `run_adk.py` includes the slack router import + mount, `.env.example` contains the Slack block.
- `nuvel new --with-telegram` → analogous.
- `nuvel new --with-teams` → verify `gateways/teams_bridge.py` lands, `requirements.txt` has the `microsoft-agents-*` lines, `.env.example` has the Teams block.
- `nuvel new --with-slack --with-telegram --with-teams` → all three present, no overlap conflicts.
- `nuvel new` (no channel flags) → output byte-identical to today's golden snapshot.
- `nuvel new --with-slack` without `--with-composio` → composio overlay is auto-applied.

**No live network in CI.** Composio / Slack / Telegram / Teams calls are mocked.

### 14. README structure

The scaffolded README gets a `## Channels` section. Each active channel adds a subsection in canonical order:

- `### Slack` — env vars, exact `composio trigger create` command, troubleshooting hint.
- `### Telegram` — env vars, exact `setWebhook` curl, ngrok hint for local dev.
- `### Teams` — env vars (both modes), Azure Bot Service registration link, the `python -m ...gateways.teams_bridge` run command, Agents Playground link for local dev.

Each subsection is gated on the corresponding overlay being applied, so the README only documents what was actually generated.

The base nuvel `nuvel new` README template gains a one-liner under "Customizing": `Use --with-slack, --with-telegram, or --with-teams to expose this agent on a chat platform.`

### 15. Documentation outside the scaffold

`README.md` at the repo root: add a one-paragraph "Messaging gateways" subsection under existing feature highlights, linking to `docs/superpowers/specs/2026-05-09-messaging-gateways-design.md`.

`CONTRIBUTING.md`: brief note that channel handlers live under `nuvel/backends/adk/templates_overlays/gateway-*/` and follow the overlay convention.

---

## Open questions / explicitly deferred

- **Cross-platform identity** — punted. Each platform user is a distinct ADK user_id in v1.
- **Custom (non-Composio) Slack option** — punted. Add as `--slack-mode={composio|custom}` in v1.x if demand exists.
- **Channels for non-ADK backends** — flags are reserved at the CLI level; backend-specific scaffolders error out cleanly when any are set.
- **Streaming replies** — v1.x extension. The gateway abstractions in `_common.py` accept a stream-aware variant cleanly because each handler already owns the message lifecycle.
- **Outbound retry queue** — v1.x extension. Single attempt + log in v1.

---

## Appendix A — File inventory

**Files added (in nuvel CLI source):**

```
nuvel/backends/adk/templates_overlays/gateway-base/
    {{agent_package}}/gateways/__init__.py
    {{agent_package}}/gateways/_common.py
nuvel/backends/adk/templates_overlays/gateway-slack/
    {{agent_package}}/gateways/slack.py
nuvel/backends/adk/templates_overlays/gateway-telegram/
    {{agent_package}}/gateways/telegram.py
nuvel/backends/adk/templates_overlays/gateway-teams/
    {{agent_package}}/gateways/teams_bridge.py
tests/test_gateway_slack.py
tests/test_gateway_telegram.py
tests/test_gateway_teams_bridge.py
tests/test_scaffold_gateways.py
docs/superpowers/specs/2026-05-09-messaging-gateways-design.md  (this doc)
```

**Files modified:**

```
nuvel/cli.py                                          # --with-slack/--with-telegram/--with-teams flags, validation, success summary
nuvel/backends/adk/scaffold.py                        # accept new flags, apply gateway overlays, populate placeholders
nuvel/backends/adk/templates/run_adk.py               # {{gateway_imports}}, {{gateway_mounts}}, /gateways in PUBLIC_PREFIXES
nuvel/backends/adk/templates/requirements.txt        # {{gateway_requirements}} placeholder
nuvel/backends/adk/templates/{{agent_package}}/.env.example  # {{gateway_env_block}} placeholder (or equivalent)
nuvel/backends/adk/templates/README.md.tmpl          # {{gateway_readme_section}} placeholder
nuvel/backends/claude_agent_sdk/scaffold.py           # accept channel flags, error if any are set
nuvel/backends/anthropic_managed_agents/scaffold.py   # accept channel flags, error if any are set
README.md                                             # link to this spec under feature list
CONTRIBUTING.md                                       # note overlay convention for new channels
```

## Appendix B — Reference: v1 source

The v1 Teams bridge is preserved at `reference/teams-v1/data-analysis-agent/run_m365_bridge.py` while this design is being implemented. After the Teams overlay lands, `reference/` is deleted from the branch (it is `.gitignore`-clean, only present in the worktree for the implementer's convenience).
