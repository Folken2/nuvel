# Meta-Agent

An ADK agent that creates production-ready Google ADK agents from natural language descriptions.

## How it works

You describe what you want — goal, tasks, tools — and the meta-agent:

1. **Scaffolds** a complete project from a production-tested skeleton (FastAPI server, plugin chain, resilience, tracing, caching)
2. **Generates** custom system prompts, function tools, and domain skills
3. **Validates** the structure and reports any issues
4. **Iterates** based on your feedback

Every generated agent inherits battle-tested infrastructure from the [data-analysis-agent](https://github.com/albertfolch-renal/data-analysis-agent) — you only need to describe the brain.

## Quick Start

```bash
# Clone
git clone https://github.com/Folken2/meta-agent.git
cd meta-agent

# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Add your OPENROUTER_API_KEY to .env

# Run
DEV_MODE=true python run_adk.py
```

The agent runs at `http://localhost:8000`. Use the `/run_sse/` endpoint or the ADK web UI:

```bash
make dev-ui   # ADK web UI with all plugins loaded
make dev      # Custom entrypoint (production-like, no UI)
make test     # Run tests
```

## Example

> "Create a Kubernetes monitoring agent that checks pod health, queries logs, and alerts on anomalies"

The meta-agent will:
- Scaffold `generated-agents/k8s-monitor-agent/`
- Generate tools: `get_pod_status`, `query_pod_logs`, `check_anomalies`
- Write skills: `k8s-alerting/SKILL.md` with escalation patterns
- Create a system prompt tailored to k8s operations
- Validate everything is wired correctly

## Architecture

```
meta-agent/
├── meta_agent/
│   ├── agent.py              # LlmAgent with tools + SkillToolset
│   ├── prompt/instructions.py # "You are an ADK agent builder"
│   ├── tools/                 # scaffold, write_file, read_file, list_files, validate
│   ├── skills/                # 5 ADK knowledge skills (loaded on demand)
│   │   ├── adk-agent-patterns/
│   │   ├── adk-prompt-engineering/
│   │   ├── adk-tool-creation/
│   │   ├── adk-skill-creation/
│   │   └── adk-callbacks-hitl/
│   ├── templates/             # Production skeleton stamped out for each new agent
│   ├── plugins/               # 10 plugins (see Plugin Chain below)
│   └── config/                # LiteLLM/OpenRouter config
├── scaffold.py                # CLI: python scaffold.py <agent-name>
├── run_adk.py                 # FastAPI server
└── generated-agents/          # Output directory
```

### Key Design Decisions

- **Template-based scaffolding** — Every generated agent inherits a proven production skeleton (plugins, circuit breakers, rate limiting, structured logging, SSE streaming). The meta-agent only generates the brain.
- **SkillToolset for knowledge** — The meta-agent loads ADK expertise on demand via progressive disclosure (L1/L2/L3), not a monolithic prompt. This keeps context usage efficient.
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

## Plugin Chain

Every generated agent ships with a full plugin chain — cross-cutting concerns that apply to all interactions without touching agent code.

| Plugin | Type | What it does |
|--------|------|-------------|
| **CostGuardPlugin** | Budget | Calculates USD cost per LLM call, enforces per-session budget limits |
| **TracePlugin** | Observability | Raw event JSONL + consolidated conversation JSON for eval pipelines |
| **ConsoleLoggerPlugin** | Observability | Color-coded terminal output for all lifecycle events |
| **ToolEventsPlugin** | Observability | Structured tool execution events for SSE streaming |
| **ContextFilterPlugin** | Performance | Keeps last N invocations in context window (default: 10) |
| **CachePlugin** | Performance | Session-scoped caching for specific tools with TTL |
| **ResiliencePlugin** | Resilience | Circuit breaker and rate limiting for tool calls |
| **ReflectAndRetryToolPlugin** | Resilience | Self-healing tool retry with LLM reflection (max 3) |
| **SaveFilesAsArtifactsPlugin** | Features | Saves user-uploaded files as session artifacts |
| **MemoryPlugin** | Features | Markdown file-based long-term memory across sessions |

### Cost Tracking & Budget Guard

The CostGuardPlugin tracks LLM costs using a `pricing.json` config file and optionally enforces per-session budget limits.

**How it works:**
1. Each LLM call's token count is multiplied by the model's per-token price from `pricing.json`
2. Cost is logged to the terminal and stored in traces (`cost_usd` per call, `total_cost_usd` in summary)
3. If `COST_GUARD_BUDGET` is set and the session cost exceeds it, further LLM calls are blocked with a friendly message

**Maintaining `pricing.json`:**

The pricing config lives at `meta_agent/plugins/pricing.json` (or `<agent>/plugins/pricing.json` for generated agents). Edit it to add or update model pricing — no code changes needed:

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

### Traces for Self-Improvement Evals

The trace system captures two layers:

```
traces/
  2026-04-06_<session>.jsonl          # Raw events (per-event, for debugging)
  conversations/
    2026-04-06_<session>.json         # Consolidated record (per-conversation, for evals)
```

The consolidated JSON includes: full system prompt, user input, LLM thinking/reasoning, response, tool calls with args/results, token usage, cost, and timing — everything an eval agent needs to score quality and drive improvements.

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

133 tests covering scaffold, file tools, validation, memory, cost guard, conversation traces, and end-to-end pipeline.

## Roadmap

- **V1 (current):** Local agent generation with scaffold + validate + iterate
- **V2:** Self-improvement eval pipeline consuming conversation traces
- **V3:** GitHub integration — create repos, push generated agents, set up CI

## License

MIT
