# CLI reference

All `nuvel` subcommands and flags.

## `nuvel new`

Scaffold a new agent project.

```bash
nuvel new <name> [options]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<name>` | *(required)* | Kebab-case agent name. Lowercase letters, digits, single hyphens; must start with a letter; max 40 chars. |
| `--framework`, `-f` | `adk` | Backend framework. One of `adk`, `claude-agent-sdk`, `anthropic-managed-agents`. |
| `--output-dir` | `./generated-agents` (or `$AGENTS_OUTPUT_DIR`) | Parent directory for the new agent. |
| `--description` | `""` | Short agent description, baked into the README and the agent's metadata. |
| `--system-prompt` | `""` | Inline system prompt seed. Overrides the per-overlay default frame. |
| `--persona` | off | *(adk only)* Activate the persona overlay (SOUL.md, AWAKENING.md, author_skill, complete_awakening). For agents meant to live for months. |
| `--with-composio` | off | *(adk only)* Wire the Composio Tool Router MCP (~1000 toolkits via one hosted endpoint). |
| `--with-slack` | off | *(adk only)* Add a Slack gateway via Composio Slackbot. **Implies `--with-composio`.** |
| `--with-telegram` | off | *(adk only)* Add a Telegram gateway (webhook + Bot API outbound). |
| `--with-teams` | off | *(adk only)* Add an MS Teams gateway as a separate aiohttp sidecar. |

### Validation

- `<name>` must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. Underscores, spaces, leading digits, double hyphens, trailing hyphens, and any uppercase character all fail.
- `--with-slack`, `--with-telegram`, `--with-teams` are accepted on non-ADK backends but exit with a clear "not yet supported on this backend" error message.

### Examples

```bash
# Bare ADK agent
nuvel new my-agent

# ADK agent with Composio tools and a Slack channel
nuvel new my-agent --with-slack

# Claude Agent SDK agent
nuvel new my-agent --framework claude-agent-sdk

# Persona-driven agent on Telegram with ~1000 tools
nuvel new soulful-bot --persona --with-composio --with-telegram

# Custom output dir
nuvel new my-agent --output-dir ./agents
```

## `nuvel skills`

Inspect the bundled knowledge skills.

### `nuvel skills list`

```bash
nuvel skills list [--framework <fw>]
```

Lists all bundled skills for the chosen framework. Each line shows the slug and a one-line description.

### `nuvel skills search`

```bash
nuvel skills search <query> [--framework <fw>]
```

Substring match (case-insensitive) against skill slug, name, and description.

## `nuvel doctor`

Diagnose the nuvel install and (if run inside a generated agent) that agent's configuration.

```bash
nuvel doctor [--path <dir>]
```

Output is a grouped checklist with `[OK]` / `[WARN]` / `[FAIL]` lines. Exits non-zero if any check fails.

| Flag | Description |
|---|---|
| `--path` | Run agent-mode checks against this directory instead of the cwd. |

**What it checks:**

- **Install** — Python version, core deps importable (`yaml`, `fastapi`, `uvicorn`, `dotenv`, `litellm`), framework SDKs per extra (`google-adk`, `claude-agent-sdk`, `anthropic`), `git` on PATH.
- **Agent** *(when run inside a generated agent)* — framework auto-detected from `requirements.txt`; verifies `.env` exists and the right keys are present (`OPENROUTER_API_KEY` for ADK, `ANTHROPIC_API_KEY` for Claude SDK, plus gateway/Composio keys if those overlays are wired); checks Docker daemon if a `Dockerfile` is present.

## `nuvel traces`

Inspect the JSONL trace logs every ADK agent emits. Auto-discovers `./traces`, `generated-agents/*/traces`, and `$TRACE_DIR`; extra paths can be passed with `--source`.

Four subcommands. All accept `--source/-s <dir>` (repeatable) plus the filters `--agent <substring>` and `--since <YYYY-MM-DD|ISO>`.

### `nuvel traces list`

```bash
nuvel traces list [--limit N] [--agent <q>] [--since <date>] [-s <dir>]
```

One row per run, newest first: when, agent, trace_id prefix, duration, LLM/tool counts, tokens, cost, input snippet. Default limit is 50 (`--limit 0` for all).

### `nuvel traces show`

```bash
nuvel traces show <id> [--all] [-s <dir>]
```

Full event timeline for one run. `<id>` is matched as a prefix against `trace_id` or `session_id`. Output is indented by `agent_depth` so sub-agent transfers and nested calls are visible.

### `nuvel traces stats`

```bash
nuvel traces stats [--agent <q>] [--since <date>] [-s <dir>]
```

Per-agent aggregates: runs, LLM calls, tool calls, tokens, total duration, total cost. Prints a cost-coverage warning at the bottom when runs have token usage but no cost — the symptom of a model missing from `pricing.json`. Run `nuvel doctor` to verify.

### `nuvel traces errors`

```bash
nuvel traces errors [--recent N] [--agent <q>] [--since <date>] [-s <dir>]
```

Surfaces failure rate across agents. Three error shapes are kept distinct:

- `llm_error` — LiteLLM gave up after its internal retries (transient infra).
- `tool_exception` — Python raised inside a tool body (likely a bug).
- `tool_end{status=error}` — tool returned a structured error result (often user-facing: bad input, auth).

Default view is a three-pane summary: by tool (with error rate against total invocations), by model (`llm_error` only), and by `error_type`. `--recent N` switches to a forensic view: last N error events newest-first with timestamps, agent labels, and message previews.

## `nuvel pricing`

Inspect and sync model pricing.json against OpenRouter — treated as the single source of truth for model pricing, including direct-provider ids that route through OR.

All subcommands accept `--target/-t {template|agent|<path>}`. Default: the active generated agent's `<pkg>/plugins/pricing.json` if cwd is one, else the bundled template under `nuvel/backends/adk/templates/`.

### `nuvel pricing list`

```bash
nuvel pricing list [-t {template|agent|<path>}]
```

Prints the resolved file path plus every entry with input/output prices in `$/Mtok`. Stable sorted so diffs stay readable.

### `nuvel pricing sync`

```bash
nuvel pricing sync [--dry-run] [-t {template|agent|<path>}]
```

Fetches the OpenRouter model catalog and **refreshes the prices of entries already in pricing.json**. Does not pull OR's full ~360-model catalog. Use `add <model>` to bring a new model in explicitly.

Prints a diff: added / updated / unchanged / not-on-OpenRouter. `--dry-run` skips the write. Comment keys (anything starting with `_`) are preserved.

### `nuvel pricing add`

```bash
nuvel pricing add <model> [--dry-run] [-t {template|agent|<path>}]
```

Add (or refresh) a single model by its OpenRouter id (e.g. `anthropic/claude-opus-4.7`). Exits non-zero if the model isn't on OpenRouter.

## `nuvel dashboard`

Open the local web dashboard over your trace logs.

```bash
nuvel dashboard [--host HOST] [--port PORT] [--source DIR ...] [--demo] [--no-open]
```

| Flag | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Localhost by design. |
| `--port` | `8765` | Bind port. |
| `--source`, `-s` | auto-discovery | Same semantics as `nuvel traces --source`. Repeatable. |
| `--demo` | off | Load bundled demo fixtures instead of real traces. |
| `--no-open` | off | Don't open the browser automatically. |

Two pages: a home view (hero + headline stats + recent activity) and a per-run detail with the thinking timeline. Live updates push new runs as the watcher sees them.

## `nuvel eval`

Online trace scorer. Heuristics-first, judge-on-pass. Writes `scored.jsonl` siblings into each trace directory; the dashboard joins on `trace_id` and surfaces a score column.

```bash
nuvel eval score [--since 7d] [--agent X] [--force] [--dry-run] [--max-cost-usd 1.00]
nuvel eval report [--since 7d] [--agent X]
nuvel eval worst  [-n 10] [--agent X]
nuvel eval drift  [--window-days 7] [--threshold 0.1]
```

### `score`

Run heuristics on every discovered trace; call the LLM judge on runs that survive the heuristic floor (so `no_assistant_output` / `incomplete_trace` skip the judge for free). Idempotent — runs with a matching `trace_id` + `scorer_version` are skipped unless `--force` is given.

| Flag | Default | Description |
|---|---|---|
| `--source / -s` | discover | Extra trace directory to scan. Repeatable. |
| `--max-cost-usd` | `1.00` | Hard cap on judge spend. Once crossed, remaining runs score with heuristics only. |
| `--concurrency` | `5` | Max simultaneous judge calls. |
| `--force` | off | Rescore every run regardless of existing `scored.jsonl` rows. |
| `--dry-run` | off | Compute scores but write nothing. |

Resolves the judge model in this order: per-agent `generated-agents/<agent>/evals/rubric.yaml` `judge.model` → `EVAL_JUDGE_MODEL` env var → `DEFAULT_FAST_MODEL`. Calls go through `litellm.acompletion`, so any provider-prefixed id works.

### `report`

Per-agent summary table: run count, mean overall, mean per component, most common flag.

### `worst`

N worst-scoring runs ranked by `overall`, with judge notes inline. The triage entry point: start your day with `nuvel eval worst -n 10`.

### `drift`

Rolling-window comparison per agent. Computes mean `overall` for the last `--window-days` and compares against the same-length window before it. Exits with code `2` if any agent crosses `--threshold` — so a future CI / webhook layer can react without further parsing.

| Flag | Default | Description |
|---|---|---|
| `--window-days` | `7` | Length of each rolling window. |
| `--threshold` | `0.1` | Flag drift when `|delta| >= threshold`. |

## `nuvel run`

Launch the meta-agent — an interactive ADK agent that helps you scaffold, configure, and ship other agents.

```bash
nuvel run [--dev]
```

| Flag | Description |
|---|---|
| `--dev` | Sets `DEV_MODE=true` for the meta-agent's session service (in-memory; no Postgres needed). |

The meta-agent itself is a nuvel-scaffolded ADK agent. Its source is under `nuvel/` in the repository.

## Programmatic API

The CLI is a thin wrapper over `scaffold_agent()` in each backend module. If you want to invoke scaffolding from Python:

```python
from nuvel.backends.adk.scaffold import scaffold_agent

result = scaffold_agent(
    name="my-agent",
    output_dir="./out",
    description="...",
    persona=False,
    with_composio=True,
    with_slack=True,    # implies with_composio
    with_telegram=False,
    with_teams=False,
)
# result["status"] == "ok" | "error"
# on success: result["path"], result["files_created"], etc.
```

The `claude_agent_sdk` and `anthropic_managed_agents` backends export the same function signature. Channel flags on those backends return `{"status": "error", "message": ...}` if any are set.
