# CLI reference

All `nuvel` subcommands and flags.

## `nuvel agent create`

Scaffold a new agent project.

```bash
nuvel agent create <name> [options]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<name>` | *(required)* | Kebab-case agent name. Lowercase letters, digits, single hyphens; must start with a letter; max 40 chars. |
| `--framework`, `-f` | `adk` | Backend framework. One of `adk`, `buzz`, `hermes`. |
| `--output-dir` | `./generated-agents` (or `$AGENTS_OUTPUT_DIR`) | Parent directory for the new agent. |
| `--description` | `""` | Short agent description, baked into the README and the agent's metadata. |
| `--system-prompt` | `""` | Inline system prompt seed. Overrides the per-overlay default frame. |
| `--persona` | off | *(adk only)* Activate the persona overlay (SOUL.md, AWAKENING.md, author_skill, complete_awakening). For agents meant to live for months. |
| `--with-composio` | off | *(adk only)* Wire the Composio Tool Router MCP (~1000 toolkits via one hosted endpoint). |
| `--with-slack` | off | *(adk only)* Add a Slack gateway via Composio Slackbot. **Implies `--with-composio`.** |
| `--with-telegram` | off | *(adk only)* Add a Telegram gateway (webhook + Bot API outbound). |
| `--with-teams` | off | *(adk only)* Add an MS Teams gateway as a separate aiohttp sidecar. |
| `--workflow` | off | *(adk only)* Make the root agent an ADK 2.0 `Workflow` graph (`agent_workflow.py`) instead of a single `LlmAgent`: plan → execute nodes in `mode='task'` with typed contracts and deterministic routing. `agent.py` becomes a shim re-exporting `root_agent`, so all import paths stay unchanged. |

### Validation

- `<name>` must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. Underscores, spaces, leading digits, double hyphens, trailing hyphens, and any uppercase character all fail.
- `--with-slack`, `--with-telegram`, `--with-teams`, `--workflow` are accepted on non-ADK backends but exit with a clear "not yet supported on this backend" error message.

### When to use `--workflow`

Use it when the agent's work is **genuinely multi-step with branching** — plan-then-execute, triage-then-route, draft-then-review. Skip it for a single-purpose tool-using agent; a graph you don't need is just indirection. See [Build a workflow-native agent](../how-to/build-workflow-agent.md) for a worked example, and the bundled `adk-workflow-graphs` + `adk-task-delegation` skills for the underlying patterns.

### Examples

```bash
# Bare ADK agent
nuvel agent create my-agent

# ADK agent with Composio tools and a Slack channel
nuvel agent create my-agent --with-slack

# Workflow-native agent: plan → execute graph with task-mode nodes
nuvel agent create triage-bot --workflow --description "triage issues, route to the right team"

# Persona-driven agent on Telegram with ~1000 tools
nuvel agent create soulful-bot --persona --with-composio --with-telegram

# Custom output dir
nuvel agent create my-agent --output-dir ./agents
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

The first thing to run, and the first thing to attach to a bug report. Diagnoses the nuvel install, plus the configuration of the generated agent in the cwd, or of `nuvel run` when there isn't one.

```bash
nuvel doctor [--path <dir>] [--json]
```

Output is a grouped checklist with `[OK]` / `[WARN]` / `[FAIL]` lines, and every `[WARN]` and `[FAIL]` is followed by one next step. Exits non-zero if any check fails, so it fails on a machine with no configuration yet. Secret values are never printed.

| Flag | Description |
|---|---|
| `--path` | Run against this directory instead of the cwd. |
| `--json` | Emit the same findings as JSON: `ok`, `exit_code`, `cwd`, `summary`, and `checks[]` with `section`, `name`, `status`, `detail`, `fix`, plus `zed_config` for `--with-acp` agents. `cwd` is `null` if the current directory was deleted. |

**Getting to green:**

- **Generated agent** — from the agent directory: `cp .env.example .env`, then set the provider key in `.env`, plus any value the agent's README asks for (`--with-slack` needs `COMPOSIO_API_KEY` and `COMPOSIO_WEBHOOK_SECRET`, `--with-composio` needs `COMPOSIO_API_KEY`, `--with-telegram` needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`). Exporting them instead also works.
- **Hermes profile** — the same, using the `.env.example` inside the profile directory. A platform enabled in `config.yaml` also needs its token.
- **`nuvel run`** — `export OPENROUTER_API_KEY=<your key>`. A source checkout can use `cp .env.example .env` at the repo root instead. An installed nuvel does **not** read `./.env` for `nuvel run`: it searches upward from the installed package, so doctor names the file that would be read.

**What it checks:**

- **Install** — Python version, `nuvel-cli` package metadata, every declared dependency installed at an allowed version, bundled templates and data files, core imports. `git` on PATH (warning only).
- **Agent** or **Configuration** — `.env` presence and validity, Hermes `config.yaml` validity, and the API keys the configured provider needs. The provider comes from `FAST_MODEL` / `REASONING_MODEL` (ADK and `nuvel run`) or `model.provider` (Hermes). Gateway and Composio keys if those overlays are wired, Docker if there's a `Dockerfile`, `pricing.json` coverage for ADK. A key set in the environment counts, and wins over `.env`, as it does at runtime. A provider doctor doesn't know is expected to use `<PROVIDER>_API_KEY` (so `FAST_MODEL=moonshotai/kimi-k2.5` wants `MOONSHOTAI_API_KEY`; route it as `openrouter/moonshotai/kimi-k2.5` to use OpenRouter). A missing gateway value the gateway can run without (`SLACK_BOT_TOKEN`, Teams credentials) is a warning.
- **State** — the traces dir, `AGENTS_OUTPUT_DIR` (for `nuvel run`), `~/.nuvel`, `~/.cache/nuvel`, and the nuvel data dir are writable. Directories that don't exist yet are probed through their parent and not created.
- **Network** — each configured provider answers an unauthenticated `HEAD`. No key is sent; any HTTP response under 500 counts as reachable. Failing this check fails doctor, so it exits non-zero offline. A provider with no known endpoint is a warning, not probed.
- **Caches** — evalv2 cache entries and the auto-cloned skills hub older than 30 days (warning only).
- **ACP adapter** *(for `--with-acp` agents)* — the stdio `initialize` handshake.

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

### `nuvel traces recipes`

```bash
nuvel traces recipes [-s <dir>]
```

Lists recipe candidates by category from the `_recipes/` tree written by the Skills MCP server (`record_recipe_signal`, `share_recipe`): recipes, tenants, uses, success rate, corrections, and the title of the latest shared version (`(structure only)` when the user did not share text). The feedback directory is always searched; `--source` adds more roots. Counters come from each recipe's latest weekly snapshot (`nuvel.recipe_signal/2`) per tenant. Tenant values are never printed. See [Skill run traces](skill-run-traces.md#recipe-signals-and-shared-recipes).

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

Three pages: a home view (hero + headline stats + recent activity), a per-run detail with the thinking timeline, and `/recipes`, the **Recipe candidates** view (the same data as `nuvel traces recipes`, plus recipe status and the latest recipe per category: its action sequence and slots, and the masked narrative only when the user shared text). Live updates push new runs as the watcher sees them.

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

### `variants`

```bash
nuvel eval variants [--agent <q>]
```

Lists discovered replay-variant configs. Variants live in `generated-agents/<agent>/evals/variants/*.yaml` and describe an alternate system prompt (or other model parameters) to replay against historical traces. Each entry shows the variant name, version, and which agent it belongs to.

### `replay`

```bash
nuvel eval replay <variant_name> [--agent <q>] [--since YYYY-MM-DD] [--max-cost-usd N] [--concurrency N] [--force] [--dry-run]
```

Replays a variant against historical traces and scores each replay with the same judge/rubric used by `score`. v1 replays the single LLM call (variant system prompt + historical user input + tool history) — not the full multi-turn loop. Results land in a `replays/<variant>.jsonl` sibling inside each trace directory.

| Flag | Default | Description |
|---|---|---|
| `--agent / -a` | *(all)* | Filter to one agent by substring. |
| `--since` | *(all)* | Only traces at/after this date (`YYYY-MM-DD` or ISO). |
| `--max-cost-usd` | `1.00` | Hard cap on replay + judge spend. |
| `--concurrency` | `5` | Max simultaneous replays. |
| `--force` | off | Re-replay even if already at this variant version. |
| `--dry-run` | off | Replay + score but write nothing. |

### `compare`

```bash
nuvel eval compare <variant_name> [--agent <q>]
```

Diffs a variant's replays against the baseline `scored.jsonl`. Exits with code `2` on regression — defined as `Δ overall < −0.05` for any agent — so CI can catch prompt regressions before they ship.

## `nuvel run`

Launch the meta-agent — an interactive ADK agent that helps you scaffold, configure, and ship other agents.

```bash
nuvel run [--dev]
```

| Flag | Description |
|---|---|
| `--dev` | Sets `DEV_MODE=true` for the meta-agent's session service (in-memory; no Postgres needed). |

The meta-agent itself is a nuvel-scaffolded ADK agent. Its source is under `nuvel/` in the repository.

## `nuvel-mcp serve`

Standalone Skills MCP server, shipped as its own dependency-free package (`nuvel-mcp`). `nuvel mcp serve` delegates to it with the same flags.

```bash
uvx nuvel-mcp serve --skills-dir ./skills-hub
nuvel-mcp serve [--skills-dir DIR] [--tools A,B] [--theme T] [--require CAP] [--with-org-memory DSN_OR_PATH] [--feedback-dir DIR] [--trace-store local|MODULE:FACTORY] [--transport stdio|http] [--host H] [--port P] [--allow-remote] [--no-auth] [--require-auth]
nuvel-mcp client create --name NAME [--redirect-uri URI ...]
nuvel-mcp client list [--json]
nuvel-mcp client delete CLIENT_ID
nuvel-mcp token revoke TOKEN_OR_CLIENT_ID
nuvel-mcp clients label CLIENT_ID [--person P] [--company C] [--notes N]
nuvel-mcp clients list [--json]
nuvel-mcp report [--days 7] [--json]
```

`clients` and `report` need `NUVEL_MCP_DATABASE_URL`: `clients label` records who is behind an OAuth client id (an empty value clears a field), `clients list` shows each client id with its label, last usage and events in the last 30 days, and `report` prints the usage report (clients, calls, tools, errors, latency, client apps, countries, skill runs, recipe adoption). What the usage log holds is listed in [Usage insights](../how-to/deploy-skills-mcp.md#usage-insights-what-is-collected).

| Flag | Description |
|---|---|
| `--skills-dir DIR` | Skills hub to serve. Optional: without it the server starts in feedback-only mode and exposes just the feedback tools (`record_feedback`, `check_skill_health`, `propose_improvement`, `record_run_trace`). `nuvel mcp serve` used to serve the current directory when it was omitted; pass `--skills-dir .` to keep that. |
| `--tools A,B` | Comma-separated allow-list of tool names to expose. Default: every tool on stdio; over HTTP `search_skills`, `list_skills`, `list_themes`, `get_skill`, `fence_value`, `record_run_trace` and `check_skill_health`. `record_feedback`, `propose_improvement` and `install_skill` are exposed over HTTP only when named here. |
| `--trace-store` | Where `record_run_trace` keeps traces: `local` (JSONL under `<feedback-dir>/_runs/`, the default) or `module:factory`, a zero-argument callable returning a `TraceStore`. See [Skill run traces](skill-run-traces.md). |
| `--transport stdio\|http` | `stdio` (default) or `http`: Streamable HTTP on `/mcp`, legacy HTTP+SSE on `/sse` + `/messages`, and `GET /healthz`. Both need only the standard library. |
| `--host H`, `--port P` | HTTP bind address. Default `127.0.0.1:8080` (`--port 0` picks a free port). |
| `--allow-remote` | Allow a non-loopback `--host`. Requires `NUVEL_MCP_PUBLIC_URL` and GitHub OAuth credentials. |
| `--no-auth` | HTTP only: disable OAuth for local development. Refused unless `--host` is loopback. There is then no principal, so traces carry no `tenant` and `check_skill_health` aggregates all tenants, as over stdio. |
| `--require-auth` | stdio only: gate every request on the access token in `$NUVEL_MCP_ACCESS_TOKEN`. HTTP is always gated unless `--no-auth`. |

HTTP requires an OAuth 2.1 access token by default. The tenant of each run trace is the token's OAuth `client_id` (for an interactive login, the `client_id` plus a hash of the user). Operators create clients with `client create` (the secret is printed once) and revoke access with `client delete` or `token revoke`. Tuning and deployment settings (`NUVEL_MCP_PUBLIC_URL`, `NUVEL_OAUTH_*`, `NUVEL_MCP_REQUEST_DEADLINE`, `NUVEL_MCP_READ_TIMEOUT`, `NUVEL_MCP_MAX_CONNECTIONS`, ...) are listed in [Deploy the Skills MCP server over HTTP](../how-to/deploy-skills-mcp.md).

`nuvel-mcp` has no runtime dependencies and no extras. The old `nuvel.mcp` import path is deprecated; import `nuvel_mcp` instead.

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
    workflow=False,     # adk only — Workflow graph instead of a single LlmAgent
)
# result["status"] == "ok" | "error"
# on success: result["path"], result["files_created"], etc.
```

The `buzz` and `hermes` backends export a `scaffold_agent()` with the same core arguments plus their own (`hermes` also accepts `with_litellm`, only to reject it). Channel flags on those backends return `{"status": "error", "message": ...}` if any are set.
