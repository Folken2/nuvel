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
| `REASONING_MODEL` | no | Override the default reasoning model (e.g. `openrouter/google/gemini-3.1-pro-preview`). |
| `OPENROUTER_REFERER` | no | Custom OpenRouter attribution header. Defaults to a per-agent value. |
| `LLM_NUM_RETRIES` | no | Retries per LLM call on transient errors (429 / 503 / timeout). Default `3`. Set globally via `litellm.num_retries`. |
| `LLM_REQUEST_TIMEOUT` | no | Per-LLM-call timeout in seconds. Default `120`. Set globally via `litellm.request_timeout`. |

## Observability / traces

Every ADK agent writes one JSONL line per lifecycle event under `TRACE_DIR`. Inspect via [`nuvel traces`](cli.md#nuvel-traces).

| Var | Default | Description |
|---|---|---|
| `TRACE_ENABLED` | `true` | Set to `false` to disable all tracing. |
| `TRACE_DIR` | `./traces` | Directory for JSONL trace files. |
| `TRACE_DB` | `false` | `true` = also write traces to PostgreSQL (`agent_traces` table). Requires `SESSION_SERVICE_URI`. |

### Per-field truncation caps

All in characters. Tool args default high so the full agent input (`query`, `parameters`, …) is captured for schema auditing — everything else is sized for "enough to debug, not a transcript".

| Var | Default | Applies to |
|---|---|---|
| `TRACE_MAX_ARGS_CHARS` | `50000` | `tool_start.args` (verbatim agent inputs) |
| `TRACE_MAX_RESULT_CHARS` | `10000` | `tool_end.result` |
| `TRACE_MAX_RESPONSE_CHARS` | `10000` | `llm_response.response_text` |
| `TRACE_MAX_THINKING_CHARS` | `10000` | `llm_response.thinking` |
| `TRACE_MAX_SYSTEM_INSTR_CHARS` | `50000` | `llm_request.system_instruction` |
| `TRACE_MAX_ERROR_CHARS` | `2000` | `llm_error.error_message`, `tool_exception.error_message` |

## Cost guard

Tracks per-call USD cost using `<pkg>/plugins/pricing.json`, propagates `state.cost_guard` for SSE consumers, and (optionally) blocks runs that would exceed a session budget. Block events surface as `cost_guard_intervention` in traces.

| Var | Default | Description |
|---|---|---|
| `COST_GUARD_BUDGET` | `0` | Max USD per session. `0` = unlimited. |
| `COST_GUARD_PRICING` | auto | Override the path to `pricing.json`. Default uses the bundled file alongside the plugin. |

Keep `pricing.json` fresh with [`nuvel pricing sync`](cli.md#nuvel-pricing-sync).

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

## Cron / scheduled jobs (opt-in)

Embedded scheduler ticks every minute and runs due jobs in fresh ADK sessions. Jobs and outputs persist on disk; on Railway, attach a Volume (see [Deploy to Railway](../how-to/deploy-to-railway.md#cron-scheduled-jobs-on-railway)).

| Var | Default | Description |
|---|---|---|
| `NUVEL_CRON_ENABLED` | `0` | `1` = start the scheduler in the agent's FastAPI lifespan. Off → no scheduler, but `/cron/*` HTTP endpoints and the `cronjob` tool still work for storage. |
| `NUVEL_CRON_TICK_SECONDS` | `60` | Scheduler tick interval. Lower for tests; higher to reduce load. |
| `NUVEL_CRON_DIR` | `~/.nuvel/cron` | Where `jobs.json`, `output/`, and `.tick.lock` live. Override on Railway to point at a Volume mount. |
| `NUVEL_CRON_RUNNING` | *(internal)* | Set by the scheduler during a cron-spawned run. The `cronjob` tool checks this to refuse mutations from inside cron sessions (recursion guard). Don't set manually. |

## Voice transcription (Slack + Telegram)

Opt-in audio transcription. When enabled, voice memos are converted to text before reaching the agent and the audio attachment is replaced with `[Voice memo, M:SS]: <transcript>`. Teams is not yet supported.

| Var | Default | Description |
|---|---|---|
| `GATEWAY_TRANSCRIBE_AUDIO` | `0` | `1` = enable transcription. Off → audio falls through unchanged. |
| `GATEWAY_TRANSCRIBE_PROVIDER` | `openai` | `openai` (Whisper) or `groq` (Whisper, faster/cheaper). |
| `GATEWAY_TRANSCRIBE_MODEL` | `whisper-1` / `whisper-large-v3` | Override the model. Defaults depend on provider. |
| `GATEWAY_TRANSCRIBE_TIMEOUT_SEC` | `60` | HTTP timeout for the provider call. |
| `OPENAI_API_KEY` | — | Required when `GATEWAY_TRANSCRIBE_PROVIDER=openai`. |
| `GROQ_API_KEY` | — | Required when `GATEWAY_TRANSCRIBE_PROVIDER=groq`. |

On provider error / missing key / oversized audio, the user message becomes `[Voice memo received but transcription failed]` so the turn isn't silently dropped.

## Eval harness (`nuvel eval`)

The eval harness scores discovered traces with deterministic heuristics plus an LLM judge. Heuristics run on every run; the judge only fires when no hard-floor flag (e.g. `no_assistant_output`) condemned the run already. Scored output is written to `scored.jsonl` siblings of the trace files.

| Var | Default | Description |
|---|---|---|
| `EVAL_JUDGE_MODEL` | `DEFAULT_FAST_MODEL` from `nuvel/_defaults.py` (currently `openrouter/moonshotai/kimi-k2.5`) | Litellm-style model id used by the judge. Per-agent `evals/rubric.yaml` overrides this; `EVAL_JUDGE_MODEL` overrides only the in-code default. |

The judge calls `litellm.acompletion`, so any provider-prefixed id works. Cost per call typically lands well under `$0.001` with Kimi K2.5; budget the whole batch via `nuvel eval score --max-cost-usd 1.00`.

## Skill curator (opt-in plugin)

The `SkillCuratorPlugin` is registered in the generated agent's plugin chain but stays inert until explicitly enabled. When active, it watches each run and — on complex turns — proposes new skills or patches to existing ones. Proposals are written to `~/.nuvel/skill-proposals/` for human review; nothing is auto-applied.

| Var | Default | Description |
|---|---|---|
| `NUVEL_SKILL_CURATOR` | `0` | `1` = enable the plugin. Off → no LLM calls, no proposals. |
| `NUVEL_SKILL_CURATOR_MIN_TOOLS` | `5` | Trigger threshold: minimum tool calls in a single run. |
| `NUVEL_SKILL_CURATOR_MIN_EVENTS` | `12` | Trigger threshold: minimum events in a single run. |
| `NUVEL_SKILL_CURATOR_MIN_ERRORS` | `3` | Trigger threshold: same tool failing this many times. |
| `NUVEL_SKILLS_DIR` | `<pkg>/skills/` | Directory enumerated to give the curator a view of existing skills. |
| `NUVEL_SKILL_PROPOSALS_DIR` | `~/.nuvel/skill-proposals/` | Where proposals are written. Always outside the repo by default. |
