<p align="center">
  <img src="docs/assets/banner.png" alt="nuvel — production-ready agents, your way" width="100%">
</p>

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
  <img alt="nuvel" src="docs/assets/logo-light.svg" width="240">
</picture>

**Production-ready agents, your way.**

Skills, scaffolder, or meta-agent — build agents on Google ADK, Buzz, or Hermes Agent with the workflow you already have.

[![Tests](https://github.com/Folken2/nuvel/actions/workflows/tests.yml/badge.svg)](https://github.com/Folken2/nuvel/actions/workflows/tests.yml)
[![Docs](https://img.shields.io/badge/docs-folken2.github.io%2Fnuvel-blue)](https://folken2.github.io/nuvel/)
[![GitHub stars](https://img.shields.io/github/stars/Folken2/nuvel?style=flat)](https://github.com/Folken2/nuvel/stargazers)

</div>

<!--
  Demo placeholder. Record with `vhs demo.tape` or `asciinema rec`, then
  drop the resulting .gif at docs/assets/demo.gif and uncomment:

  <p align="center">
    <img src="docs/assets/demo.gif" alt="nuvel demo" width="800">
  </p>
-->

## What is nuvel?

nuvel is an open-source toolkit for building production-ready agents across the agent frameworks that matter — Google ADK, Buzz, and Hermes Agent. It ships in three shapes — knowledge skills, a CLI scaffolder, and an autonomous meta-agent — and you use whichever fits the way you already work.

The skills follow the [Anthropic skills format](https://www.anthropic.com/news/skills), so they plug into the coding agent you already use: **Claude Code**, **Codex**, **Cursor**, **OpenClaw**, **Hermess Agent**, and any other agent that supports the format. The CLI stamps out a battle-tested skeleton tuned per framework — an opinionated 17-plugin chain for ADK, a leaner Buzz skeleton with Nostr relay and Bitcoin/Lightning tools, and a Hermes Agent skeleton with platform gateways. The meta-agent does it for you autonomously from a natural-language description.

## Frameworks

| Framework | Flag | Knowledge skills | Where the agent runs |
| --- | --- | --- | --- |
| [Google ADK](https://google.github.io/adk-docs/) | `--framework adk` *(default)* | 15 skills (agent patterns, tool creation, prompt engineering, callbacks/HITL, streaming, skill design, Composio Tool Router, workflow graphs, task delegation, long-horizon guardrails/sessions, cron isolation, org memory retrieval, self-improvement) | Your server (OpenRouter + LiteLLM) |
| [Buzz](https://github.com/Folken2/nuvel) | `--framework buzz` | Nostr relay, Bitcoin/Lightning tools, on-chain leaderboard | Your server (OpenRouter) |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | `--framework hermes` | Platform gateways (Telegram, Slack, Teams), skill-driven config | Your server (OpenRouter)

## Features

- **Three shapes, one toolkit** — drop the skills into your coding agent, run the scaffolder, or let the meta-agent build the whole thing autonomously.
- **Production skeleton, not a toy** — every generated agent ships with FastAPI, framework-appropriate observability (17-plugin chain for ADK), Dockerfile, Railway config, and tests.
- **Portable knowledge skills** — skills across the supported frameworks, with progressive disclosure so they don't bloat your context.
- **Self-evolving agents** — `--persona` ships a SOUL.md / awakening pattern for agents meant to live for months and develop a stable character. Inspired by OpenClaw. *(ADK only.)*
- **~1000 integrations** — `--with-composio` wires the Composio Tool Router for one-line access to Gmail, GitHub, Slack, Notion, Calendar, and more. *(ADK only.)*
- **ACP-compatible & CLI-runnable** — `--with-acp` adds an [Agent Client Protocol](https://agentclientprotocol.com) adapter (JSON-RPC over stdio, the protocol editors like Zed use to drive an agent as a subprocess) plus a local terminal CLI. Run it as an editor subprocess (`python -m <pkg>.acp`) or straight from the shell (`python -m <pkg>.cli "…"` one-shot, or a REPL). The adapter honors the editor's session: `mcpServers` passed in `session/new` (stdio / HTTP / SSE) are wired in as tools, the agent reads/writes through the editor's filesystem view (unsaved buffers included) when the client supports it, and sensitive tool calls are gated by an editor approve/deny prompt (`session/request_permission`, tunable via `ACP_PERMISSION_MODE`). Both entrypoints reuse the same plugin-wired Runner as the FastAPI server. *(ADK only.)*
- **Messaging gateways** — scaffold an agent reachable from Slack, Telegram, or MS Teams with one flag (`--with-slack`, `--with-telegram`, `--with-teams`). See [docs/superpowers/specs/2026-05-09-messaging-gateways-design.md](docs/superpowers/specs/2026-05-09-messaging-gateways-design.md). *(ADK only.)*
  - **Slack:** Multimodal — forwards user images/files (size and count caps via `GATEWAY_MAX_ATTACHMENT_*`) and uploads agent artifacts back to chat. See the per-channel README for details.
  - **Telegram:** Multimodal — forwards user images/files (size and count caps via `GATEWAY_MAX_ATTACHMENT_*`) and uploads agent artifacts back to chat. See the per-channel README for details.
  - **Teams:** Multimodal — forwards user images/files (size and count caps via `GATEWAY_MAX_ATTACHMENT_*`) and surfaces inline agent artifacts back to chat. Inline agent artifacts only; saved artifacts via `tool_context.save_artifact(...)` are surfaced on Slack and Telegram but not yet on the Teams sidecar. See the per-channel README for details.
- **Unified slash commands** — `/help`, `/new`, `/usage`, `/stop`, `/personality`, `/cron` work the same way on every gateway. Add your own with one decorator and it's live on all channels. *(ADK only.)*
- **Scheduled automations** — opt-in cron scheduler runs jobs on relative (`30m`), interval (`every 2h`), or cron-expression (`0 9 * * *`) schedules. Manage from chat (`/cron add ...`), HTTP (`POST /cron/jobs`), or natural language (the `cronjob` tool). Delivery to origin chat, local file, or any configured Slack/Telegram channel. *(ADK only.)*
- **Runtime personalities** — drop a markdown overlay at `~/.nuvel/personalities/<name>.md` and switch with `/personality <name>` per session. Lighter than `--persona` and composes with it. *(ADK only.)*
- **Voice memo transcription** — set `GATEWAY_TRANSCRIBE_AUDIO=1` and Slack/Telegram voice notes are transcribed via Whisper (OpenAI or Groq) before reaching the agent. *(ADK only.)*
- **Self-improving skills loop** — opt-in `SkillCuratorPlugin` watches each run and proposes new skills (or patches to existing ones) to `~/.nuvel/skill-proposals/` for human review. Never auto-applies. *(ADK only.)*
- **`nuvel doctor`** — one command diagnoses the install and any generated agent in your cwd: missing env vars, broken deps, framework SDKs, Docker reachability.
- **Vendor-neutral by default** — pick OpenRouter (ADK) or Anthropic direct (Claude Agent SDK), optional PostgreSQL for sessions, runs on any host that takes a Docker container.

## Bot Fleet Management

nuvel ships with a built-in bot fleet management system for orchestrating Hermes-backed agents.

**Single-bot operations:**

```
nuvel bots create <name> [--skills <refs>] [--model <model>]
nuvel bots list
nuvel bots info <name>
nuvel bots chat <name> <message>
nuvel bots logs <name> [-n 50]
nuvel bots delete <name>
nuvel bots send <from> <to> <msg>
```

**Skill discovery:**

```
nuvel bots skills list [--category <theme>]
nuvel bots skills search <query>
```

**Fleet deployment (multi-bot):**

```
nuvel fleet deploy <manifest.yaml>
nuvel fleet list
nuvel fleet status <name>
nuvel fleet destroy <manifest.yaml>
nuvel fleet update-vision <fleet> <path>
```

Fleet manifests support VISION.md (shared constitution symlinked into every bot), a Manager bot with fleet governance skills, and per-bot cron routines.

Example manifest:

```yaml
name: "my-company"
vision: |
  # Fleet Constitution
  All bots follow these shared rules...
manager:
  name: manager-bot
  model: anthropic/claude-sonnet-4
default_model: deepseek/deepseek-v4-flash
bots:
  - name: triage-bot
    skills: [customer/triage-agent]
    routines:
      - schedule: "0 8 * * 1-5"
        task: "Review yesterday's logs"
```

## Quick Install

**Use the skills with Claude Code:**

```bash
git clone https://github.com/Folken2/nuvel.git
# Skills live in nuvel/backends/adk/skills/ and a ready-made SKILL.md is at .claude/skills/nuvel/
```

**Install the CLI from PyPI:**

```bash
pip install nuvel-cli
```

> The PyPI distribution is named **`nuvel-cli`** (the bare `nuvel` name was too close to an existing package). The CLI command and all imports stay `nuvel` — `pip install nuvel-cli` then `nuvel agent create my-agent`.

**Or install from source (always available):**

```bash
git clone https://github.com/Folken2/nuvel.git
cd nuvel
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Getting Started

### 1. Scaffold an agent

```bash
# Google ADK (default)
nuvel agent create k8s-monitor --description "checks pod health, queries logs, alerts on anomalies"

# Buzz
nuvel agent create k8s-monitor --framework buzz --description "..."

# Hermes Agent
nuvel agent create k8s-monitor --framework hermes --description "..."
```

You get a complete project at `generated-agents/k8s-monitor/` — package layout, FastAPI server, framework-appropriate plugins, Dockerfile, Railway config, and tests.

### 2. Fill in the brain

The skeleton is free; the brain is yours. Edit:

- **ADK**: `tools/` (one file per tool), `prompt/instructions.py`, `skills/`, `agent.py` (wire tools + `SkillToolset`).
- **Buzz**: `tools/` (Nostr + Lightning tools), `prompt/instructions.py`, `skills/`, `agent.py`.
- **Hermes Agent**: `tools/`, `prompt/instructions.py`, `skills/`, `agent.py` (wire platform gateways).

If you're driving Claude Code, the bundled skill at [.claude/skills/nuvel/SKILL.md](.claude/skills/nuvel/SKILL.md) walks Claude through this step-by-step.

### 3. Run it

```bash
cd generated-agents/k8s-monitor
cp .env.example .env       # add OPENROUTER_API_KEY
pip install -r requirements.txt

# ADK
DEV_MODE=true python run_adk.py

# Buzz
python server.py            # or `python run_dev.py "<prompt>"` for a quick local test

# Hermes Agent
python server.py            # or `python run_dev.py "<prompt>"` for a quick local test
```

The agent runs at `http://localhost:8000`. POST to `/run_sse/` to talk to it.

### Want the meta-agent to do all this for you?

```bash
nuvel run --dev            # http://localhost:8000
```

Then describe the agent you want; nuvel will scaffold, generate, and validate it autonomously.

## CLI

| Command | Description |
| ------- | ----------- |
| `nuvel agent create <name>` | Scaffold a new agent (default framework: `adk`) |
| `nuvel agent create <name> --framework <fw>` | Pick framework: `adk`, `buzz`, or `hermes` |
| `nuvel agent create <name> --description "..."` | Scaffold with a one-liner description |
| `nuvel agent create <name> --persona` | *(adk only)* Self-evolving agent (SOUL.md, awakening flow) |
| `nuvel agent create <name> --with-composio` | *(adk only)* Bundle ~1000 integrations via Composio Tool Router |
| `nuvel agent create <name> --with-slack` | *(adk only)* Add a Slack Events API gateway (auto-enables `--with-composio`) |
| `nuvel agent create <name> --with-telegram` | *(adk only)* Add a Telegram Bot webhook gateway |
| `nuvel agent create <name> --with-teams` | *(adk only)* Add a Microsoft Teams bot bridge via Azure Bot Service |
| `nuvel agent create <name> --workflow` | *(adk only)* Workflow-native root agent — an ADK 2.0 `Workflow` graph with task-mode nodes, typed contracts, and conditional routing |
| `nuvel agent create <name> --with-acp` | *(adk only)* Make the agent ACP-compatible (Agent Client Protocol over stdio, `python -m <pkg>.acp`) and CLI-runnable (`python -m <pkg>.cli`) |
| `nuvel agent create <name> --with-eval` | *(adk only)* Stamp a starter evalv2 suite into the agent (`skills/default/eval/suite.yaml` + a sample example) |
| `nuvel agent create <name> --output-dir ./agents` | Override the output directory |
| `nuvel skills list [--framework <fw>]` | List bundled knowledge skills for a framework |
| `nuvel skills search <term> [--framework <fw>]` | Search skills by keyword |
| `nuvel doctor [--path <dir>]` | Diagnose the install and the agent in cwd (env, deps, pricing) |
| `nuvel traces list \| show \| stats \| errors` | Inspect JSONL trace logs across all local agents |
| `nuvel pricing list \| sync \| add <model>` | Sync `pricing.json` against OpenRouter |
| `nuvel dashboard [--demo]` | Open a local web dashboard over your trace logs |
| `nuvel eval score \| report \| worst \| drift` | **Production trace scoring** — score traces (heuristics + LLM judge), surface worst runs, detect drift |
| `nuvel evalv2 init <skill> [--name <n>] [--description <d>] [--force]` | **Skill evaluation** — stamp a starter `eval/` suite into a skill |
| `nuvel evalv2 list` | List skills that ship an `eval/` suite |
| `nuvel evalv2 run <skill> [--model <m>] [--json] [--save-baseline]` | Run a skill's eval suite against a fresh executor and save the result |
| `nuvel evalv2 compare <skill> [--json] [--threshold N]` | Diff a skill's latest run against its baseline; exits 2 on regression |
| `nuvel plugins list` | Discover [Agent Plugins](https://agent-plugins.org/) in the configured `plugin_dirs` (`NUVEL_PLUGIN_DIRS`, default `./plugins`), listing each plugin's skills and MCP servers |
| `nuvel plugins load <dir>` | Load and inspect a single plugin directory |
| `nuvel plugins config` | Show the current plugin directory configuration |
| `nuvel run` | Run the meta-agent (production-style server) |
| `nuvel run --dev` | Same, with in-memory sessions for dev |

`make install` / `make dev` / `make run` / `make dev-ui` / `make skills` / `make test` are wired through to the same commands.

## Example

> "A Kubernetes monitoring agent that checks pod health, queries logs, and alerts on anomalies."

Whichever shape you used, you end up with the same project:

- `generated-agents/k8s-monitor-agent/` — full project, ready to `pip install` and run
- Tools: `get_pod_status`, `query_pod_logs`, `check_anomalies`
- Domain skills: `k8s-alerting/SKILL.md` with escalation patterns
- System prompt tailored to k8s operations
- Plugin chain wired in (cost guard, tracing, resilience, caching)

## Architecture

```
nuvel/
├── nuvel/
│   ├── agent.py               # Meta-agent: LlmAgent with tools + SkillToolset
│   ├── cli.py                 # `nuvel` CLI (new / skills / run)
│   ├── run_adk.py             # FastAPI server (launched by `nuvel run`)
│   ├── prompt/instructions.py # Meta-agent system prompt
│   ├── tools/                 # scaffold, write_file, read_file, list_files, validate
│   ├── plugins/               # Meta-agent chain (12, via Makefile PLUGIN_FLAGS)
│   ├── config/                # LiteLLM/OpenRouter config
│   └── backends/              # Per-framework scaffolders + skills
│       ├── adk/               # Google ADK backend
│       │   ├── scaffold.py
│       │   ├── templates/     # Production skeleton for ADK agents
│       │   └── skills/        # ADK knowledge skills
│       ├── buzz/              # Buzz backend (Nostr relay + Lightning)
│       │   ├── scaffold.py
│       │   ├── templates/
│       │   └── skills/
│       └── hermes/            # Hermes Agent backend
│           ├── scaffold.py
│           ├── templates/     # Platform gateway skeleton
│           └── skills/
├── .claude/skills/nuvel/      # Claude Code SKILL.md for driving the CLI
├── pyproject.toml             # Packaging + `nuvel` console script
└── generated-agents/          # Output directory
```

### Key Design Decisions

- **Template-based scaffolding** — Every generated agent inherits a proven production skeleton (plugins, circuit breakers, rate limiting, structured logging, SSE streaming). You only write the brain.
- **Skills as a portable knowledge format** — The 15 ADK skills follow the Anthropic skills format, so they work in Claude Code today and in any agent that adopts the format. Progressive disclosure (L1/L2/L3) keeps context usage efficient.
- **Scoped file operations** — All file tools are sandboxed to the output directory. No path traversal possible.

## Generated Agent Structure

Each generated agent is a standalone, runnable project:

```
generated-agents/my-agent/
├── my_agent/
│   ├── agent.py           # LlmAgent with SkillToolset
│   ├── prompt/            # Custom system prompt
│   ├── tools/             # Domain-specific tools
│   ├── skills/            # Domain skills (SKILL.md)
│   ├── contexts/          # Domain knowledge files
│   ├── plugins/           # Full production plugin chain
│   └── config/            # LiteLLM/OpenRouter config
├── run_adk.py             # FastAPI server with auth + health checks
├── requirements.txt
└── .env.example
```

Run any generated agent:

```bash
cd generated-agents/my-agent
pip install -r requirements.txt
DEV_MODE=true python run_adk.py
```

### Configuration

Every environment variable a generated agent reads is documented inline in `.env.example`, grouped by subsystem (auth, plugins, providers, memory, cron, and more). It's copied into each scaffolded project from `nuvel/backends/adk/templates/.env.example`, so that file — not this README — is the canonical reference for what to configure.

## Plugin Chain

Every generated agent ships with a full plugin chain — cross-cutting concerns that apply to all interactions without touching agent code.

| Plugin | Type | What it does |
|--------|------|-------------|
| **MemoryPlugin** | Features | Markdown file-based long-term memory across sessions |
| **CostGuardPlugin** | Budget | Calculates USD cost per LLM call, enforces per-session budget limits |
| **ContextWindowPlugin** | Observability | Tracks context-window occupancy per response (tokens used / max / %) for live UI indicators |
| **TracePlugin** | Observability | Raw event JSONL + consolidated conversation JSON for eval pipelines |
| **ContextFilterPlugin** | Performance | Keeps last N invocations in context window (default: 10) |
| **ConsoleLoggerPlugin** | Observability | Color-coded terminal output for all lifecycle events |
| **ToolEventsPlugin** | Observability | Structured tool execution events for SSE streaming |
| **ResiliencePlugin** | Resilience | Circuit breaker and rate limiting for tool calls |
| **GuardrailsPlugin** | Resilience | Halts runaway model/tool loops — no-progress and repeated-failure guards behind a shared halt latch |
| **CronIsolationPlugin** | Resilience | Headless tool-approval policy during scheduled cron runs; inert outside cron runs |
| **CachePlugin** | Performance | Session-scoped caching for specific tools with TTL |
| **ReflectAndRetryToolPlugin** | Resilience | Self-healing tool retry with LLM reflection (max 3) |
| **SaveFilesAsArtifactsPlugin** | Features | Saves user-uploaded files as session artifacts |
| **RecordingsPlugin** | Testing | Records LLM/tool interactions for ADK conformance testing; inert unless recording mode is active |
| **ReplayPlugin** | Testing | Replays recorded interactions instead of executing live calls; inert unless replay mode is active |
| **SkillCuratorPlugin** | Features | Proposes `SKILL.md` files from observed tool usage; opt-in, default off (`NUVEL_SKILL_CURATOR`) |
| **SiblingRunner** | Features | Drains fire-and-forget memory-review sibling runs at shutdown; no-op until `NUVEL_MEMORY_REVIEW_FORK=1` |

### Cost Tracking & Budget Guard

The CostGuardPlugin tracks LLM costs using a `pricing.json` config file and optionally enforces per-session budget limits.

**How it works:**
1. Each LLM call's token count is multiplied by the model's per-token price from `pricing.json`
2. Cost is logged to the terminal and stored in traces (`cost_usd` per call, `total_cost_usd` in summary)
3. If `COST_GUARD_BUDGET` is set and the session cost exceeds it, further LLM calls are blocked with a friendly message

**Maintaining `pricing.json`:**

The pricing config lives at `nuvel/plugins/pricing.json` (or `<agent>/plugins/pricing.json` for generated agents). Edit it to add or update model pricing — no code changes needed:

```json
{
  "moonshotai/kimi-k2.5": {
    "input": 0.0000005,
    "output": 0.000002
  },
  "anthropic/claude-sonnet-4": {
    "input": 0.000003,
    "output": 0.000015
  }
}
```

Keys are model IDs (matching what your LLM provider returns). The plugin auto-strips provider prefixes — `openrouter/moonshotai/kimi-k2.5` matches `moonshotai/kimi-k2.5`. Prices are in USD per token.

To find current prices: check [OpenRouter models](https://openrouter.ai/models) or your provider's pricing page.

### Context Window Tracking

The ContextWindowPlugin knows the total context window of the selected model and reports how full it is after every response — the same "context used" signal Claude Code shows. It's read-only: it never mutates the request and never blocks.

After each LLM call it publishes a `context_window` snapshot into session state, so a frontend consuming the SSE stream can render a live indicator on every agent response:

```json
{
  "model": "anthropic/claude-sonnet-4",
  "used_tokens": 12345,
  "prompt_tokens": 12000,
  "completion_tokens": 345,
  "max_tokens": 200000,
  "remaining_tokens": 187655,
  "used_pct": 6.17,
  "remaining_pct": 93.83
}
```

`used_tokens` prefers the provider's `total_token_count` (so reasoning/thinking tokens are included) and falls back to `prompt + completion`. When the model isn't in the config, percentages are omitted (`max_tokens: null`) unless `CONTEXT_WINDOW_DEFAULT` is set.

**Maintaining `context_windows.json`:**

Window sizes live at `nuvel/plugins/context_windows.json` (or `<agent>/plugins/context_windows.json` for generated agents) and use the same keys as `pricing.json`, so provider prefixes are auto-stripped (`openrouter/anthropic/claude-sonnet-4` matches `anthropic/claude-sonnet-4`):

```json
{
  "anthropic/claude-sonnet-4": 200000,
  "google/gemini-2.5-pro": 1048576
}
```

| Env var | Default | Effect |
|---------|---------|--------|
| `CONTEXT_WINDOW_CONFIG` | auto-detected | Path to a custom `context_windows.json` |
| `CONTEXT_WINDOW_DEFAULT` | `0` (unknown) | Fallback window size for models not in the config |
| `CONTEXT_WINDOW_WARN_PCT` | `0` (off) | Log a one-time warning once usage crosses this percent |

### Traces for Self-Improvement Evals

The trace system captures two layers:

```
traces/
  2026-04-06_<session>.jsonl          # Raw events (per-event, for debugging)
  conversations/
    2026-04-06_<session>.json         # Consolidated record (per-conversation, for evals)
```

The consolidated JSON includes: full system prompt, user input, LLM thinking/reasoning, response, tool calls with args/results, token usage, cost, context-window occupancy (per call, plus a `peak_context_tokens` / `peak_context_pct` in the summary), and timing — everything an eval agent needs to score quality and drive improvements.

## Configuration

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | OpenRouter API key |
| `FAST_MODEL` | `openrouter/moonshotai/kimi-k2.5` | LLM model |
| `AGENTS_OUTPUT_DIR` | `./generated-agents` | Where agents are created |
| `DEV_MODE` | `false` | In-memory sessions for dev |
| `PORT` | `8000` | Server port |
| `API_KEY` | (optional) | Bearer token auth |
| `SESSION_SERVICE_URI` | (optional) | PostgreSQL for prod sessions |

### Cost Guard

| Variable | Default | Description |
|----------|---------|-------------|
| `COST_GUARD_BUDGET` | `0` (unlimited) | Max USD per session. Set to e.g. `0.50` to cap spending |
| `COST_GUARD_PRICING` | (bundled) | Path to custom `pricing.json`. Default uses the bundled file |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACE_ENABLED` | `true` | Master on/off for all tracing |
| `TRACE_DIR` | `./traces` | Directory for JSONL + conversation trace files |
| `TRACE_DB` | `false` | Also write traces to PostgreSQL (`agent_traces` table) |
| `LOG_FORMAT` | `text` | `json` for production (structured), `text` for dev (colored) |
| `LOG_LEVEL` | `INFO` | Logging level |

### Memory

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_ENABLED` | `true` | Enable/disable long-term memory |
| `MEMORY_DIR` | `./memory` | Directory for markdown memory files |
| `MEMORY_MAX_CORE_SIZE` | `10000` | Max chars for core memory file |
| `MEMORY_MAX_TOPIC_SIZE` | `5000` | Max chars per topic file |

### Resilience

| Variable | Default | Description |
|----------|---------|-------------|
| `TOOL_RATE_LIMIT` | `5.0` | Tool calls per second (token bucket) |
| `TOOL_RATE_BURST` | `20` | Burst capacity for tool rate limiting |
| `PROTECTED_TOOLS` | (none) | Comma-separated tools with circuit breaker |
| `CONTEXT_FILTER_KEEP` | `10` | Prior invocations to keep in context window |

## Tests

```bash
make test
```

Tests cover scaffold, file tools, validation, memory, cost guard, conversation traces, and end-to-end pipeline.

## Roadmap

- **V1 (current):** Local agent generation with scaffold + validate + iterate
- **V2:** Self-improvement eval pipeline consuming conversation traces
- **V3:** GitHub integration — create repos, push generated agents, set up CI
- **V4 (proposed):** One-shot auto-deploy — `deploy_agent_tool` pushes the
  generated agent to Railway (or equivalent) and returns a live URL, so
  non-technical users go from natural-language intent to a running agent
  without leaving the conversation
- **V5 (proposed):** Managed tier — same binary, hosted. Users sign up, describe
  an agent, and get a live URL on our infrastructure (our Railway, our LLM
  keys, usage-based billing). Open-source stays the primary product and the
  community path; managed serves users who want zero ops

## Contributing

Contributions are welcome — please read [CONTRIBUTING.md](CONTRIBUTING.md)
before opening a PR. For security issues, see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Albert Folch
