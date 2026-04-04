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

The agent runs at `http://localhost:8000`. Use the `/run_sse/` endpoint or `adk web .` for the ADK web UI.

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
│   ├── plugins/               # Trace, resilience, cache, console logger
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

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | (required) | OpenRouter API key |
| `FAST_MODEL` | `openrouter/moonshotai/kimi-k2.5` | LLM model |
| `AGENTS_OUTPUT_DIR` | `./generated-agents` | Where agents are created |
| `DEV_MODE` | `false` | In-memory sessions for dev |
| `PORT` | `8000` | Server port |
| `API_KEY` | (optional) | Bearer token auth |
| `SESSION_SERVICE_URI` | (optional) | PostgreSQL for prod sessions |

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

47 tests covering scaffold, file tools, validation, and end-to-end pipeline.

## Roadmap

- **V1 (current):** Local agent generation with scaffold + validate + iterate
- **V2:** GitHub integration — create repos, push generated agents, set up CI

## License

MIT
