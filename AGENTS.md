# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project Overview

nuvel is a toolkit for generating production-ready AI agents across three frameworks:

- Google ADK, the default framework.
- Claude Agent SDK.
- Anthropic Managed Agents.

It ships as:

- Knowledge skills in `nuvel/backends/<framework>/skills/`.
- A CLI scaffolder exposed as `nuvel new`.
- A meta-agent exposed as `nuvel run`, implemented as a Google ADK `LlmAgent` in `nuvel/agent.py`.
- A Skills MCP server exposed as `nuvel mcp serve`, in `nuvel/mcp/` (stdlib only).

Generated projects are written to `generated-agents/<name>/` and are standalone runnable FastAPI apps. They should not depend on importing the local `nuvel` package at runtime.

## Common Commands

```bash
make install     # pip install -e .; exposes the nuvel console script
make test        # python -m pytest tests/ -v
make run         # nuvel run
make dev         # nuvel run --dev
make dev-ui      # adk web with all 10 plugins loaded
make skills      # nuvel skills list
```

Run a single test with:

```bash
pytest tests/test_scaffold.py::test_name -v
```

Tests use `pytest-asyncio` auto mode from `pyproject.toml`; do not add unnecessary `@pytest.mark.asyncio` decorators.

## Architecture Notes

Each backend under `nuvel/backends/<framework>/` follows the same broad shape:

- `scaffold.py` orchestrates template copying and placeholder substitution.
- `templates/` contains the project skeleton copied into generated agents.
- `skills/` contains framework-specific Anthropic-format skills.

ADK also has `templates_overlays/` for optional features such as Slack, Telegram, and Teams gateways.

When adding cross-cutting scaffolding behavior, check all three backends. ADK may implement the behavior while the other frameworks may need explicit rejection or compatibility handling.

## Template Rules

Files under backend `templates/` are text templates, not importable Python modules. Preserve placeholders such as:

- `{{agent_package}}`
- `{{agent_name}}`

Placeholders may appear in file contents and paths. Avoid edits that accidentally resolve or remove them.

## Plugin Chains

Do not confuse the two plugin chains:

- `nuvel/plugins/` is for the nuvel meta-agent itself.
- `nuvel/backends/adk/templates/{{agent_package}}/plugins/` is copied into generated ADK agents.

Changes to the generated-agent template plugin chain affect future scaffolded agents, not the running meta-agent.

## Skills

Skills are progressive-disclosure documentation, not Python code. They live under `nuvel/backends/*/skills/<name>/SKILL.md` and follow the Anthropic skills format.

Generated ADK agents should prefer the built-in `SkillToolset` instead of duplicating custom `list_skills` or `read_skill` FunctionTools.

Keep `.claude/skills/nuvel/SKILL.md` in sync with CLI flags, scaffold behavior, and agent architecture changes because it is the primary skill entry point for users driving coding agents against this repo.

## Skills MCP Server

`nuvel mcp serve` starts a stdlib-only MCP (Model Context Protocol) stdio server that exposes a skills hub — the [Nuvel Skills](https://github.com/Folken2/skills) repo, or any directory laid out the same way — to MCP clients such as Claude Code, Cursor, and Codex.

```bash
nuvel mcp serve [--skills-dir <dir>]
```

- `--skills-dir` (default: current directory) points at a skills hub. It accepts either the skills directory itself (contains `index.json`) or a repo root that contains `skills/index.json`.
- The server speaks JSON-RPC 2.0 over stdio: protocol messages on stdout (one JSON object per line), diagnostics on stderr.

It exposes:

- **Resources** — `skill://{theme}/{name}` for every skill in the hub's `index.json`, returning the full `SKILL.md`.
- **Tools** — `search_skills` (keyword search over name/description), `get_skill` (full content + frontmatter metadata by `name` or `theme/name`), and `propose_improvement` (files a structured GitHub issue proposing a skill fix).

`propose_improvement` files issues against `github.com/Folken2/skills` using the `GITHUB_TOKEN` environment variable. Without a token it degrades gracefully: the proposal is logged to stderr and returned to the caller instead of being filed.

The code lives in `nuvel/mcp/` (`server.py` = JSON-RPC protocol, `skills_loader.py` = hub discovery/loading) with the command handler in `nuvel/commands/mcp_serve.py`. It is deliberately dependency-free so it runs without the ADK/agent stack installed; keep it that way.

## Generated Agent Shape

An ADK generated agent usually looks like:

```text
generated-agents/my-agent/
+-- my_agent/
|   +-- agent.py
|   +-- prompt/
|   +-- tools/
|   +-- skills/
|   +-- contexts/
|   +-- plugins/
|   +-- config/
+-- run_adk.py
+-- .env.example
```

Generated agents should remain runnable with:

```bash
pip install -r requirements.txt
DEV_MODE=true python run_adk.py
```

## Conventions

- Python 3.11+ is required.
- The PyPI package is `nuvel-cli`, but the import and CLI command remain `nuvel`.
- The console entry point is `nuvel = "nuvel.cli:main"` in `pyproject.toml`.
- File operations exposed to the meta-agent under `nuvel/tools/` are sandboxed to `AGENTS_OUTPUT_DIR`; preserve path traversal protections.
- Commit messages use conventional prefixes such as `feat:`, `fix:`, and `docs:`.
- Keep edits scoped. Avoid broad refactors when changing scaffolding, templates, or skills.

## Cost And Trace Data

`CostGuardPlugin` reads `nuvel/plugins/pricing.json` for the meta-agent, while generated agents read their copied `plugins/pricing.json`. Provider prefixes are stripped, so a model such as `openrouter/moonshotai/kimi-k2.5` can match a pricing key like `moonshotai/kimi-k2.5`.

Traces are written under `traces/` as per-event `.jsonl` files and consolidated per-conversation `.json` files used by `nuvel eval` and `nuvel dashboard`.

## Testing Guidance

Prefer targeted tests for narrow changes and broader scaffold tests for changes that affect generated project structure, CLI flags, backend behavior, or shared safety checks.

Path sandbox changes should include or update tests in `tests/test_path_guard.py`.
