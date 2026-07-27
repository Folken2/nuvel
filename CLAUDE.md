# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

nuvel is a **toolkit for generating production-ready AI agents** across three frameworks: Google ADK (default), Claude Agent SDK, and Anthropic Managed Agents. It ships as three things at once:

1. **Knowledge skills** (Anthropic skills format) under `nuvel/backends/<fw>/skills/` — portable into any compatible coding agent.
2. **A CLI scaffolder** (`nuvel new`) that stamps out a full project skeleton from templates.
3. **A meta-agent** (`nuvel run`) — itself a Google ADK `LlmAgent` defined in [nuvel/agent.py](nuvel/agent.py) — that autonomously scaffolds + iterates on agents from a natural-language description.

Generated projects land in `generated-agents/<name>/` and are standalone runnable FastAPI apps.

## Common commands

```bash
make install     # pip install -e .  (exposes `nuvel` console script)
make test        # python -m pytest tests/ -v
make run         # nuvel run         (meta-agent, prod-style)
make dev         # nuvel run --dev   (in-memory sessions)
make dev-ui      # adk web with all 10 plugins loaded
make skills      # nuvel skills list
```

Single test: `pytest tests/test_scaffold.py::test_name -v`. Tests are async-mode auto (see `[tool.pytest.ini_options]` in [pyproject.toml](pyproject.toml)) — no `@pytest.mark.asyncio` decorators needed.

CLI surface (full table in README): `nuvel new`, `nuvel skills list|search`, `nuvel doctor`, `nuvel traces`, `nuvel pricing`, `nuvel dashboard`, `nuvel eval`, `nuvel run`.

## Architecture — the parts that span multiple files

### Three parallel backends

Every framework has the same shape under `nuvel/backends/<framework>/`:

- `scaffold.py` — orchestrates template copy + variable substitution for that framework.
- `templates/` — the actual production skeleton that gets copied into generated agents (FastAPI server, plugin chain, Dockerfile, Railway config, tests). Paths like `{{agent_package}}/` are placeholders rewritten at scaffold time.
- `skills/` — knowledge skills bundled with that framework (8 / 6 / 5 respectively).

When adding cross-cutting features (e.g. a new `--with-<channel>` flag), you almost always need to touch **all three** scaffolders: the ADK one to implement it, the other two to add it to their rejection lists. See [CONTRIBUTING.md](CONTRIBUTING.md) "Adding a new messaging-app channel" for the canonical recipe.

ADK has an additional `templates_overlays/` directory (e.g. `gateway-slack/`, `gateway-telegram/`, `gateway-teams/`, `acp/`) merged onto the base templates when the corresponding flag is set. The `acp/` overlay (`--with-acp`) adds an Agent Client Protocol adapter (`<pkg>/acp/`, stdio JSON-RPC per agentclientprotocol.com) plus a local terminal CLI (`<pkg>/cli.py`), both reusing the same `AgentHarness` Runner — so the agent is runnable as an editor subprocess and from the shell, not only as the FastAPI server.

### Two plugin chains — don't confuse them

- `nuvel/plugins/` — plugins for the **meta-agent itself** (cost guard, trace, console logger, etc.). Wired via `PLUGIN_FLAGS` in the [Makefile](Makefile) and loaded by `nuvel run`.
- `nuvel/backends/adk/templates/{{agent_package}}/plugins/` — the analogous plugin chain that gets *copied into every generated ADK agent*. Modifications here affect future scaffolded projects, not the meta-agent.

The 11 plugins (CostGuard, ContextWindow, Trace, ConsoleLogger, ToolEvents, ContextFilter, Cache, Resilience, ReflectAndRetryTool, SaveFilesAsArtifacts, Memory) follow the ADK plugin lifecycle and apply cross-cutting concerns without touching agent code.

### Skills are progressive-disclosure docs, not Python code

Skills under `nuvel/backends/*/skills/<name>/SKILL.md` follow the Anthropic skills format (L1 frontmatter description / L2 SKILL.md body / L3 referenced files). They're consumed two ways: as a runtime `SkillToolset` in generated ADK agents, and as drop-in skills for Claude Code / Cursor / Codex.

### Templates use `{{placeholder}}` substitution

Files in `templates/` containing `{{agent_package}}` or `{{agent_name}}` in either content or path are rewritten by the scaffolder. Don't treat them as importable Python — they're text templates. When editing, preserve the placeholders.

### Generated agent shape (ADK example)

```
generated-agents/my-agent/
├── my_agent/
│   ├── agent.py        # LlmAgent + SkillToolset wiring
│   ├── prompt/, tools/, skills/, contexts/
│   ├── plugins/        # full 10-plugin chain (copy of templates)
│   └── config/         # LiteLLM/OpenRouter config
├── run_adk.py          # FastAPI server with auth + health checks
└── .env.example
```

Generated agents are runnable standalone (`pip install -r requirements.txt && DEV_MODE=true python run_adk.py`) — they don't import from the `nuvel` package at runtime.

### Cost / trace data flow

`CostGuardPlugin` reads `nuvel/plugins/pricing.json` (or a generated agent's own `plugins/pricing.json`). Provider prefixes are auto-stripped — `openrouter/moonshotai/kimi-k2.5` matches the `moonshotai/kimi-k2.5` key. Traces write two layers under `traces/`: per-event `.jsonl` for debugging + consolidated per-conversation `.json` consumed by `nuvel eval` and `nuvel dashboard`.

## Conventions worth knowing

- Python 3.11+ (`requires-python = ">=3.11"`).
- Console script entry point: `nuvel = "nuvel.cli:main"` in [pyproject.toml](pyproject.toml).
- File operations exposed to the meta-agent (`nuvel/tools/`) are sandboxed to `AGENTS_OUTPUT_DIR` — path traversal is rejected. Tests live in `tests/test_path_guard.py`.
- Commit messages use conventional-style prefixes (`feat:`, `fix:`, `docs:`, …); see recent `git log`.
- PyPI package is `nuvel-cli` but the import / CLI name stays `nuvel`.
- The `.claude/skills/nuvel/SKILL.md` is the entry point users hit when driving Claude Code against this repo — keep it in sync with CLI flag changes.

## Auto-memory note (for the human reading this)

There is a project memory store at `memory/MEMORY.md` consumed by the auto-memory system in user-global CLAUDE.md. Check it on session start; one current entry says to prefer the built-in `SkillToolset` over duplicate custom `list_skills` / `read_skill` FunctionTools in generated agents.
