# CLI reference

Every `nuvel` command and every flag. `tests/test_cli_reference.py` walks the real argument parser and fails when a command, flag or argument is missing from this page, or when this page documents a command the parser no longer has.

Every example on this page was run against `nuvel 0.5.0.dev0` in a scratch directory. In the output, `~` is the scratch home directory and `~/work` the directory the command ran in. Client secrets and URL keys are cut short.

## `nuvel`

The entry point. It takes a command, and two options of its own.

```bash
nuvel [-h] [--version] <command> ...
```

| Flag | Description |
|---|---|
| `-h`, `--help` | Print help and exit `0`. Every command and subcommand below accepts it too. |
| `--version` | Print the installed `nuvel-cli` version, read from package metadata, and exit `0`. Added after 0.4.0. |

**Exit codes** (for every command): `0` on success; `2` when the command line is wrong (unknown command, missing or invalid argument), with the usage line and the error on stderr. Each command lists its other codes.

```console
$ nuvel --version
nuvel 0.5.0.dev0

$ nuvel
usage: nuvel [-h] [--version]
             {agent,new,skills,plugins,run,doctor,traces,pricing,mcp,dashboard,eval,bots,fleet,evalv2}
             ...
nuvel: error: the following arguments are required: command
```

## `nuvel agent`

Manage agents. Its only subcommand is `create`.

### `nuvel agent create`

Scaffold a new agent project.

```bash
nuvel agent create <name> [options]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<name>` | *(required)* | Kebab-case agent name. Lowercase letters, digits, single hyphens; must start with a letter; max 40 chars. |
| `--framework`, `-f` | `adk` | Backend framework. One of `adk`, `hermes`, `claude-agent-sdk`. See [Experimental](../experimental.md) for what is not supported. |
| `--output-dir` | `./generated-agents` (or `$AGENTS_OUTPUT_DIR`) | Parent directory for the new agent. |
| `--description` | `""` | Short agent description, baked into the README and the agent's metadata (`SOUL.md` on Hermes). |
| `--system-prompt` | `""` | Inline system prompt seed. Overrides the per-overlay default frame on ADK, fills `prompt/system_prompt.md` on the Claude Agent SDK, and becomes the `# Purpose` section of `SOUL.md` on Hermes. |
| `--persona` | off | *(adk only)* Activate the persona overlay (SOUL.md, AWAKENING.md, author_skill, complete_awakening). For agents meant to live for months. |
| `--with-composio` | off | *(adk only)* Wire the Composio Tool Router MCP (~1000 toolkits via one hosted endpoint). |
| `--with-slack` | off | *(adk only)* Add a Slack gateway via Composio Slackbot. **Implies `--with-composio`.** |
| `--with-telegram` | off | *(adk, hermes)* Add a Telegram gateway: webhook + Bot API outbound on ADK, `platforms.telegram` enabled in `config.yaml` on Hermes. |
| `--with-teams` | off | *(adk only)* Add an MS Teams gateway as a separate aiohttp sidecar. |
| `--workflow` | off | *(adk only)* Make the root agent an ADK 2.0 `Workflow` graph (`agent_workflow.py`) instead of a single `LlmAgent`: plan → execute nodes in `mode='task'` with typed contracts and deterministic routing. `agent.py` becomes a shim re-exporting `root_agent`, so all import paths stay unchanged. |
| `--with-eval` | off | *(adk only)* Stamp a starter evalv2 suite into the agent. |
| `--with-litellm` | off | *(adk only)* Generate a ready-to-run LiteLLM proxy alongside the agent. |

**Exit codes:** `0` scaffolded; `1` invalid name, target directory already exists, or a flag the chosen backend does not support.

#### Validation

- `<name>` must match `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. Underscores, spaces, leading digits, double hyphens, trailing hyphens, and any uppercase character all fail.
- A backend refuses a flag it doesn't support rather than silently ignoring it: the command exits non-zero with an error naming the flag and the backend. [Pick a backend](../how-to/pick-a-backend.md#nuvel-feature-support) lists which flags each backend honours.

#### When to use `--workflow`

Use it when the agent's work is **genuinely multi-step with branching** — plan-then-execute, triage-then-route, draft-then-review. Skip it for a single-purpose tool-using agent; a graph you don't need is just indirection. See [Build a workflow-native agent](../how-to/build-workflow-agent.md) for a worked example, and the bundled `adk-workflow-graphs` + `adk-task-delegation` skills for the underlying patterns.

#### Examples

```console
$ nuvel agent create my-agent
Agent scaffolded at: generated-agents/my-agent
Files created: 75
Framework: adk

$ nuvel agent create support-bot --framework hermes --with-telegram --output-dir ./agents
Agent scaffolded at: agents/support-bot
Files created: 4
Framework: hermes
Channels: telegram

$ nuvel agent create My_Agent
Error: adk backend: Invalid agent name 'My_Agent'. Use lowercase letters, digits, and single hyphens; must start with a letter and cannot end with a hyphen.

$ nuvel agent create tg-bot --framework hermes --with-slack
Error: hermes backend: --with-slack is not supported for the hermes backend — Telegram (--with-telegram) is the platform this backend wires. Use --framework adk for the other gateways.
```

More invocations:

```bash
nuvel agent create my-agent --with-slack          # ADK agent with Composio tools and a Slack channel
nuvel agent create triage-bot --workflow --description "triage issues, route to the right team"
nuvel agent create soulful-bot --persona --with-composio --with-telegram
```

## `nuvel new`

Deprecated alias for [`nuvel agent create`](#nuvel-agent-create): the same arguments, `<name>`, `--framework`, `-f`, `--output-dir`, `--description`, `--system-prompt`, `--persona`, `--with-composio`, `--with-slack`, `--with-telegram`, `--with-teams`, `--workflow`, `--with-eval` and `--with-litellm`, with the same defaults and the same exit codes. Use `nuvel agent create` instead.

**Exit codes:** as `nuvel agent create`.

```console
$ nuvel new old-style --output-dir ./agents
Agent scaffolded at: agents/old-style
Files created: 75
Framework: adk
```

## `nuvel skills`

Inspect the knowledge skills bundled with each backend.

### `nuvel skills list`

List every bundled skill for a framework, one line per skill: its slug and the start of its description.

```bash
nuvel skills list [--framework <fw>]
```

| Flag | Default | Description |
|---|---|---|
| `--framework`, `-f` | `adk` | Whose skills to list: `adk`, `hermes` or `claude-agent-sdk`. |

**Exit codes:** `0`, including when there are no skills (`No skills found.`).

```console
$ nuvel skills list -f hermes
  hermes  Building and operating Hermes agents — the SOUL.md / config.yaml / skills pro...
```

### `nuvel skills search`

Case-insensitive substring match against each skill's slug, name and description.

```bash
nuvel skills search <query> [--framework <fw>]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<query>` | *(required)* | Text to look for. |
| `--framework`, `-f` | `adk` | Whose skills to search. |

**Exit codes:** `0`, including when nothing matches (`No skills found.`).

```console
$ nuvel skills search workflow
  adk-agent-patterns   Agent architecture patterns for Google ADK 2.x — when to reach for a single L...
  adk-workflow-graphs  Build graph-based agents with ADK 2.0 `Workflow` — declare nodes and edges, r...
```

## `nuvel plugins`

Discover and inspect Agent Plugins: directories with a `plugin.json` manifest that bundle skills and MCP servers.

### `nuvel plugins list`

Load every plugin found in the configured plugin directories and print each one's name, version, description, skills and MCP servers. Errors in a plugin are printed under it; registry errors go to stderr.

```bash
nuvel plugins list
```

Takes no arguments. The directories come from `NUVEL_PLUGIN_DIRS` (comma-separated), else `./plugins`; see `nuvel plugins config`.

**Exit codes:** `0`, including when no plugins are found.

```console
$ nuvel plugins list
No plugins found.
```

### `nuvel plugins load`

Load a single plugin directory and print it as `nuvel plugins list` would.

```bash
nuvel plugins load <dir>
```

| Argument | Description |
|---|---|
| `<dir>` | Path to a plugin directory, the one holding `plugin.json`. |

**Exit codes:** `0` loaded; `1` not a directory, or the manifest is invalid.

```console
$ nuvel plugins load ./claude-code-plugin
● claude-code v1.0.0
    Nuvel integration plugin for Anthropic Claude Code CLI
    skills:  cc-agent, cc-review, cc-setup
    mcp:     nuvel-orgmemory (stdio)
```

### `nuvel plugins config`

Print the plugin directories `nuvel plugins list` searches, and whether each exists.

```bash
nuvel plugins config
```

Takes no arguments.

**Exit codes:** `0`.

```console
$ nuvel plugins config
Plugin directories (set NUVEL_PLUGIN_DIRS to override):
  ~/work/plugins  [missing]
```

## `nuvel run`

Launch the meta-agent: an ADK agent served over HTTP that scaffolds, configures and iterates on other agents from a description. It runs `python -m nuvel.run_adk` in a child process and listens on `$PORT` (default 8000).

```bash
nuvel run [--dev]
```

| Flag | Description |
|---|---|
| `--dev` | Sets `DEV_MODE=true` for the meta-agent's session service (in-memory; no Postgres needed). |

**Exit codes:** the server process's own exit code; `0` after a clean shutdown.

```console
$ nuvel run --dev
[ADK] WARNING: No API_KEY set — endpoints are unauthenticated
[ADK] Endpoints added: /health, /
[ADK] Meta-Agent ready: http://0.0.0.0:8000
INFO:     Started server process [270]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://:8000 (Press CTRL+C to quit)
```

The meta-agent itself is a nuvel-scaffolded ADK agent. Its source is under `nuvel/` in the repository.

## `nuvel doctor`

The first thing to run, and the first thing to attach to a bug report. Diagnoses the nuvel install, plus the configuration of the generated agent in the cwd, or of `nuvel run` when there isn't one.

```bash
nuvel doctor [--path <dir>] [--json]
```

Output is a grouped checklist with `[OK]` / `[WARN]` / `[FAIL]` lines, and every `[WARN]` and `[FAIL]` is followed by one next step. Secret values are never printed.

| Flag | Description |
|---|---|
| `--path` | Run against this directory instead of the cwd. |
| `--json` | Emit the same findings as JSON: `ok`, `exit_code`, `cwd`, `summary`, and `checks[]` with `section`, `name`, `status`, `detail`, `fix`. `cwd` is `null` if the current directory was deleted. |

**Exit codes:** `0` when no check fails (warnings allowed); `1` when any check fails, so it fails on a machine with no configuration yet.

```console
$ cd generated-agents/my-agent && nuvel doctor
nuvel doctor
cwd: ~/work/generated-agents/my-agent

Install
  [OK]    Python version — 3.12.3
  [OK]    nuvel-cli package — 0.5.0.dev0
  [OK]    declared dependencies — 17 checked
  [OK]    bundled files
  [OK]    import yaml
  [OK]    import fastapi
  [OK]    import uvicorn
  [OK]    import dotenv
  [OK]    import litellm
  [OK]    import composio
  [OK]    import google.adk
  [OK]    git on PATH

Agent (adk)
  [FAIL]  .env file — missing
          → Create it: cp ~/work/generated-agents/my-agent/.env.example ~/work/generated-agents/my-agent/.env
  [FAIL]  OPENROUTER_API_KEY — not set in .env or the environment
          → A real key starts with sk-or- (get one at https://openrouter.ai/keys). Add OPENROUTER_API_KEY=<your key> to ~/work/generated-agents/my-agent/.env
  [WARN]  Docker available — docker CLI not on PATH
          → Only needed to build the image; install from https://docs.docker.com/get-docker/
  [OK]    pricing.json: FAST_MODEL — openrouter/moonshotai/kimi-k2.5 (default) via 'moonshotai/kimi-k2.5'
  [OK]    pricing.json: REASONING_MODEL — openrouter/google/gemini-3.1-pro-preview (default) via 'google/gemini-3.1-pro-preview'

State
  [OK]    traces dir — ~/work/generated-agents/my-agent/traces
  [OK]    ~/.nuvel — ~/.nuvel
  [OK]    ~/.cache/nuvel — ~/.cache/nuvel
  [OK]    nuvel data dir — ~/.local/share/nuvel

Network
  [OK]    openrouter reachable — https://openrouter.ai/api/v1/models (HTTP 200)

Caches
  [OK]    evalv2 sample cache — empty
  [OK]    evalv2 results cache — empty
  [OK]    skills hub clone — not cloned

Summary
  22 ok, 1 warn, 2 fail
```

**Getting to green:**

- **Generated agent** — from the agent directory: `cp .env.example .env`, then set the provider key in `.env`, plus any value the agent's README asks for (`--with-slack` needs `COMPOSIO_API_KEY` and `COMPOSIO_WEBHOOK_SECRET`, `--with-composio` needs `COMPOSIO_API_KEY`, `--with-telegram` needs `TELEGRAM_BOT_TOKEN` and `TELEGRAM_WEBHOOK_SECRET`). Exporting them instead also works.
- **Hermes profile** — the same, using the `.env.example` inside the profile directory. A platform enabled in `config.yaml` also needs its token.
- **`nuvel run`** — `export OPENROUTER_API_KEY=<your key>`. A source checkout can use `cp .env.example .env` at the repo root instead. An installed nuvel does **not** read `./.env` for `nuvel run`: it searches upward from the installed package, so doctor names the file that would be read.

**What it checks:**

- **Install** — Python version, `nuvel-cli` package metadata, every declared dependency installed at an allowed version, bundled templates and data files, core imports. `git` on PATH (warning only).
- **Agent** or **Configuration** — `.env` presence and validity, Hermes `config.yaml` validity, and the API keys the configured provider needs. The provider comes from `FAST_MODEL` / `REASONING_MODEL` (ADK and `nuvel run`) or `model.provider` (Hermes). Gateway and Composio keys if those overlays are wired, Docker if there's a `Dockerfile`, `pricing.json` coverage for ADK. A key set in the environment counts, and wins over `.env`, as it does at runtime; in a Hermes profile `.env` wins instead, because Hermes loads it with `override=True`, and only `<profile>/.env` is read. An API key must also look like one: a value with spaces or non-ASCII characters, under 16 characters, too repetitive, or without its provider's prefix (`sk-or-`, `sk-ant-`, `sk-`, `gsk_`) fails, and so does a placeholder. `COMPOSIO_API_KEY` follows the same rules, with no fixed prefix. A provider doctor doesn't know is expected to use `<PROVIDER>_API_KEY` (so `FAST_MODEL=moonshotai/kimi-k2.5` wants `MOONSHOTAI_API_KEY`; route it as `openrouter/moonshotai/kimi-k2.5` to use OpenRouter). A missing gateway value the gateway can run without (`SLACK_BOT_TOKEN`, Teams credentials) is a warning.
- **State** — the traces dir, `AGENTS_OUTPUT_DIR` (for `nuvel run`), `~/.nuvel`, `~/.cache/nuvel`, and the nuvel data dir are writable. Directories that don't exist yet are probed through their parent and not created.
- **Network** — each configured provider answers an unauthenticated `HEAD`. No key is sent; any HTTP response under 500 counts as reachable. Failing this check fails doctor, so it exits non-zero offline. A provider with no known endpoint is a warning, not probed. Ollama is probed at `OLLAMA_API_BASE` when that's set, and at `localhost:11434` otherwise.
- **Caches** — evalv2 cache entries and the auto-cloned skills hub older than 30 days (warning only).

## `nuvel traces`

Inspect the JSONL trace logs every ADK agent emits. Auto-discovers `./traces`, `generated-agents/*/traces`, and `$TRACE_DIR`; `--source`/`-s <dir>` (repeatable) adds more.

The filters these subcommands share:

- `--agent`/`-a <q>` keeps runs whose agent name contains `<q>`.
- `--since <date>` keeps runs at or after a date, `YYYY-MM-DD` or ISO 8601. Give it a UTC offset (`2026-05-15T00:00:00+00:00`): a date or time without one currently fails with a traceback against trace files whose timestamps carry an offset.

### `nuvel traces list`

One row per run, newest first: when, agent, trace_id prefix, duration, LLM/tool counts, tokens, cost, input snippet.

```bash
nuvel traces list [--limit N] [--agent <q>] [--since <date>] [-s <dir>]
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | discovery | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | all | Agent name substring. |
| `--since` | all | Only runs at or after this date. |
| `--limit`, `-n` | `50` | Max rows; `0` for all. |

**Exit codes:** `0`, including when no run is found (`No runs found.`).

```console
$ nuvel traces list
WHEN                 AGENT                 ID            DUR    LLM  TOOLS  TOK    COST     INPUT
-------------------  --------------------  ------------  -----  ---  -----  -----  -------  ----------------------------------------
2026-05-15 10:31:14  (local)/outlook_king  7ae5f4645a1e  5.5s   1    -      2.0k   $0.0000  sort the last 50 messages by urgency
2026-05-15 10:00:00  (local)/meta_agent    b1aef763f4e4  10.7s  1    2      3.3k   $0.0000  I want a background agent for technical…
2026-05-15 09:58:00  (local)/ppt_king      c3d4e5f6a7b8  23.5s  2    2      12.3k  $0.0552  slide deck about Q3 results, board-frie…

3 run(s).
```

### `nuvel traces show`

Full event timeline for one run, indented by `agent_depth` so sub-agent transfers and nested calls are visible.

```bash
nuvel traces show <id> [--all] [-s <dir>]
```

| Argument / flag | Description |
|---|---|
| `<id>` | A `trace_id` or `session_id`, or a prefix of one. |
| `--all` | When the prefix matches several runs, show every one instead of refusing. |
| `--source`, `-s` | Extra trace directory to scan. Repeatable. |

**Exit codes:** `0` shown; `1` no run matches, or several match and `--all` was not given.

```console
$ nuvel traces show c3d4
── c3d4e5f6a7b8c9d0 ─ (local)/ppt_king
   file:     ~/work/traces/cost_breakdown.jsonl
   session:  demo-session-3
   started:  2026-05-15T09:58:00+00:00
   ended:    2026-05-15T09:58:23.530+00:00
   duration: 23.5s
   llm/tool: 2/2  tokens: 12350  cost: $0.0552
   input:    slide deck about Q3 results, board-friendly tone

   09:58:00+00:  run_start      slide deck about Q3 results, board-friendly tone
   09:58:00.010  agent_start    agent=ppt_king
   09:58:00.020  llm_request    model=openrouter/google/gemini-3.1-pro-preview  msgs=2  tools=3
   09:58:12.000  llm_response   latency=11980ms  tok=6020  calls=0
   09:58:12.010  tool_start     tool=build_slide  args=title='Q3 in one line', subtitle='Revenue +18% YoY, margin …
   09:58:13.450  tool_end       tool=build_slide  status=success  dur=1440ms
   09:58:13.460  llm_request    model=openrouter/google/gemini-3.1-pro-preview  msgs=5  tools=3
   09:58:22.100  llm_response   latency=8640ms  tok=6330  calls=0
   09:58:22.110  tool_start     tool=add_chart  args=type='waterfall', data='segments'
   09:58:23.500  tool_end       tool=add_chart  status=success  dur=1390ms
   09:58:23.520  agent_end      agent=ppt_king
   09:58:23.530  run_end        dur=23530ms  llm=2  tool=2  tok=12350  cost=$0.0552
```

### `nuvel traces stats`

Per-agent aggregates: runs, LLM calls, tool calls, tokens, total duration, total cost. Prints a cost-coverage warning at the bottom when runs have token usage but no cost — the symptom of a model missing from `pricing.json`. Run `nuvel doctor` to verify.

```bash
nuvel traces stats [--agent <q>] [--since <date>] [-s <dir>]
```

| Flag | Description |
|---|---|
| `--source`, `-s` | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | Agent name substring. |
| `--since` | Only runs at or after this date. |

**Exit codes:** `0`, including when no run is found.

```console
$ nuvel traces stats
AGENT    RUNS  LLM  TOOLS  TOKENS  DUR    COST
-------  ----  ---  -----  ------  -----  -------
(local)  3     4    4      17.6k   39.8s  $0.0552

Total: 3 run(s), 17.6k tokens, $0.0552
```

### `nuvel traces errors`

Surfaces failure rate across agents. Three error shapes are kept distinct:

- `llm_error` — LiteLLM gave up after its internal retries (transient infra).
- `tool_exception` — Python raised inside a tool body (likely a bug).
- `tool_end{status=error}` — tool returned a structured error result (often user-facing: bad input, auth).

```bash
nuvel traces errors [--recent N] [--agent <q>] [--since <date>] [-s <dir>]
```

| Flag | Description |
|---|---|
| `--source`, `-s` | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | Agent name substring. |
| `--since` | Only runs at or after this date. |
| `--recent`, `-r` | Instead of the summary, print the last N error events, newest first, with timestamps, agent labels and message previews. |

The default view is a three-pane summary: by tool (with error rate against total invocations), by model (`llm_error` only), and by `error_type`.

**Exit codes:** `0`, including when there are no errors.

```console
$ nuvel traces errors --recent 2
2026-05-15 10:31:19  (local)  7ae5f4645a1e  llm_error  model=openrouter/moonshotai/kimi-k2.5  type=RateLimitError
    OpenRouter returned 429 after 3 retries
2026-05-15 10:31:17  (local)  7ae5f4645a1e  tool_exception  tool=send_email  type=SMTPAuthenticationError
    535 Authentication failed for service@example.com
```

### `nuvel traces recipes`

Lists recipe candidates by category from the `_recipes/` tree written by the Skills MCP server (`record_recipe_signal`, `share_recipe`): recipes, tenants, uses, success rate, corrections, and the title of the latest shared version (`(structure only)` when the user did not share text). Counters come from each recipe's latest weekly snapshot (`nuvel.recipe_signal/2`) per tenant. Tenant values are never printed. See [Skill run traces](skill-run-traces.md#recipe-signals-and-shared-recipes).

```bash
nuvel traces recipes [-s <dir>]
```

| Flag | Description |
|---|---|
| `--source`, `-s` | Extra directory to search for a `_recipes` tree. Repeatable. The feedback directory is always searched. |

**Exit codes:** `0`, including when there is nothing to list.

```console
$ nuvel traces recipes
No recipe signals found.
```

## `nuvel pricing`

Inspect and sync model pricing.json against OpenRouter — treated as the single source of truth for model pricing, including direct-provider ids that route through OR.

Every subcommand takes `--target`/`-t {template|agent|<path>}`. Default: the active generated agent's `<pkg>/plugins/pricing.json` if cwd is one, else the bundled template under `nuvel/backends/adk/templates/`.

### `nuvel pricing list`

Print the resolved file path plus every entry with input/output prices in `$/Mtok`, sorted so diffs stay readable.

```bash
nuvel pricing list [-t {template|agent|<path>}]
```

| Flag | Description |
|---|---|
| `--target`, `-t` | Which pricing.json: `template`, `agent`, or a path. |

**Exit codes:** `0`; `1` when the file is missing or unreadable.

```console
$ nuvel pricing list -t ./pricing.json
file: ~/work/pricing.json
models: 10
  anthropic/claude-opus-4        in $15.0000/Mtok  out $75.0000/Mtok
  anthropic/claude-sonnet-4      in $3.0000/Mtok  out $15.0000/Mtok
  deepseek/deepseek-r1           in $0.7000/Mtok  out $2.5000/Mtok
  google/gemini-2.5-flash        in $0.3000/Mtok  out $2.5000/Mtok
  google/gemini-2.5-pro          in $1.2500/Mtok  out $10.0000/Mtok
  google/gemini-3-flash-preview  in $0.5000/Mtok  out $3.0000/Mtok
  google/gemini-3.1-pro-preview  in $2.0000/Mtok  out $12.0000/Mtok
  moonshotai/kimi-k2.5           in $0.4000/Mtok  out $1.9000/Mtok
  openai/gpt-4o                  in $2.5000/Mtok  out $10.0000/Mtok
  openai/gpt-4o-mini             in $0.1500/Mtok  out $0.6000/Mtok
```

### `nuvel pricing sync`

Fetches the OpenRouter model catalog and **refreshes the prices of entries already in pricing.json**. Does not pull OR's full catalog. Use `add <model>` to bring a new model in explicitly.

```bash
nuvel pricing sync [--dry-run] [-t {template|agent|<path>}]
```

| Flag | Description |
|---|---|
| `--target`, `-t` | Which pricing.json: `template`, `agent`, or a path. |
| `--dry-run` | Print the diff without writing. |

Prints a diff: added / updated / unchanged / not-on-OpenRouter. Comment keys (anything starting with `_`) are preserved.

**Exit codes:** `0`; `1` when the file is missing or unreadable, OpenRouter is unreachable, or the write fails.

```console
$ nuvel pricing sync --dry-run -t ./pricing.json
file: ~/work/pricing.json
fetching https://openrouter.ai/api/v1/models …
OpenRouter returned 455 models

~ updated (1):
    moonshotai/kimi-k2.5
      input  $0.4000/Mtok → $0.5000/Mtok
      output $1.9000/Mtok → $2.5000/Mtok
? not on OpenRouter (3) — kept as-is:
    anthropic/claude-opus-4
    anthropic/claude-sonnet-4
    deepseek/deepseek-r1
= unchanged: 6

(dry-run — no file written)
```

### `nuvel pricing add`

Add (or refresh) a single model by its OpenRouter id.

```bash
nuvel pricing add <model> [--dry-run] [-t {template|agent|<path>}]
```

| Argument / flag | Description |
|---|---|
| `<model>` | OpenRouter model id, e.g. `anthropic/claude-opus-4.7`. |
| `--target`, `-t` | Which pricing.json: `template`, `agent`, or a path. |
| `--dry-run` | Print the result without writing. |

**Exit codes:** `0` added, updated or unchanged; `1` when the model isn't on OpenRouter, the file is missing or unreadable, OpenRouter is unreachable, or the write fails.

```console
$ nuvel pricing add anthropic/claude-opus-4.7 --dry-run -t ./pricing.json
file: ~/work/pricing.json
fetching https://openrouter.ai/api/v1/models …
added: anthropic/claude-opus-4.7  in $5.0000/Mtok  out $25.0000/Mtok
(dry-run — no file written)
```

## `nuvel mcp`

The Skills MCP server and its operator commands. They come from the separate `nuvel-mcp` package: `nuvel mcp <command>` and `nuvel-mcp <command>` take the same flags and behave the same. `nuvel-mcp` has no runtime dependencies and no extras. The old `nuvel.mcp` import path is deprecated; import `nuvel_mcp` instead.

Tuning and deployment settings (`NUVEL_MCP_PUBLIC_URL`, `NUVEL_OAUTH_*`, `NUVEL_MCP_REQUEST_DEADLINE`, `NUVEL_MCP_READ_TIMEOUT`, `NUVEL_MCP_MAX_CONNECTIONS`, ...) are listed in [Deploy the Skills MCP server over HTTP](../how-to/deploy-skills-mcp.md).

### `nuvel mcp serve`

Serve a skills hub to MCP clients: skills as resources (`skill://{theme}/{name}`) and tools (`search_skills`, `list_skills`, `list_themes`, `get_skill`, `install_skill`, `fence_value`, `propose_improvement`, `record_feedback`, `check_skill_health`, `record_run_trace`). Runs until stdin closes (stdio) or it gets SIGTERM / Ctrl+C (HTTP).

```bash
uvx nuvel-mcp serve --skills-dir ./skills-hub
nuvel mcp serve [--skills-dir DIR] [--tools A,B] [--theme T] [--require CAP] [--with-org-memory DSN_OR_PATH] [--feedback-dir DIR] [--trace-store auto|local|postgres|MODULE:FACTORY] [--recipe-store auto|local|postgres|MODULE:FACTORY] [--transport stdio|http] [--host H] [--port P] [--allow-remote] [--no-auth] [--require-auth]
```

| Flag | Description |
|---|---|
| `--skills-dir` | Skills hub to serve: a directory with `index.json`, or a repo root containing `skills/index.json`. Optional: without it the server starts in feedback-only mode and exposes just the feedback tools (`record_feedback`, `check_skill_health`, `propose_improvement`, `record_run_trace`). `nuvel mcp serve` used to serve the current directory when it was omitted; pass `--skills-dir .` to keep that. |
| `--tools` | Comma-separated allow-list of tool names to expose. Default: every tool on stdio; over HTTP `search_skills`, `list_skills`, `list_themes`, `get_skill`, `fence_value`, `record_run_trace` and `check_skill_health`. `record_feedback`, `propose_improvement` and `install_skill` are exposed over HTTP only when named here. |
| `--theme` | Expose only the skills in one theme (e.g. `hr`, `sales`). |
| `--require` | Expose only skills whose `requires` frontmatter declares every listed capability. Repeatable; each value may be comma-separated. |
| `--with-org-memory` | Resolve `{{ var_name }}` placeholders in skills against the org memory: a Postgres connection string (contains `://`) or an org-graph YAML path. Falls back to `NUVEL_ORG_MEMORY_DSN` / `NUVEL_ORG_GRAPH_PATH`. |
| `--feedback-dir` | The local feedback store: `record_feedback` outcomes, queued `propose_improvement` proposals, and what `check_skill_health` reads. Default: `$NUVEL_FEEDBACK_DIR`, then `~/.nuvel/feedback`. |
| `--trace-store` | Where `record_run_trace` keeps traces: `postgres` (`$NUVEL_MCP_DATABASE_URL`), `local` (JSONL under `<feedback-dir>/_runs/`) or `module:factory`, a zero-argument callable returning a `TraceStore`. Default `auto`: `postgres` when `NUVEL_MCP_DATABASE_URL` is set, else `local`. See [Skill run traces](skill-run-traces.md). |
| `--recipe-store` | Where recipe signals and recipes are kept: `postgres`, `local` (files under `<feedback-dir>/_recipes/`) or `module:factory`, returning a `RecipeStore`. Default `auto`. |
| `--transport` | `stdio` (default) or `http`: Streamable HTTP on `/mcp`, legacy HTTP+SSE on `/sse` + `/messages`, and `GET /healthz`. Both need only the standard library. |
| `--host`, `--port` | HTTP bind address. Default `127.0.0.1:8080` (`--port 0` picks a free port). |
| `--allow-remote` | Allow a non-loopback `--host`. Requires `NUVEL_MCP_PUBLIC_URL` and either GitHub OAuth credentials or `NUVEL_OAUTH_LOGIN=none` (URL keys and client credentials only). |
| `--no-auth` | HTTP only: disable OAuth for local development. Refused unless `--host` is loopback. There is then no principal, so traces carry no `tenant` and `check_skill_health` aggregates all tenants, as over stdio. |
| `--require-auth` | stdio only: gate every request on the access token in `$NUVEL_MCP_ACCESS_TOKEN`. HTTP is always gated unless `--no-auth`. |

HTTP requires an OAuth 2.1 access token by default. The tenant of each run trace is the token's OAuth `client_id` (for an interactive login, the `client_id` plus a hash of the user). Operators create clients with `nuvel mcp client create` (the secret is printed once) and revoke access with `nuvel mcp client delete` or `nuvel mcp token revoke`. `propose_improvement` files issues against `github.com/Folken2/skills` when `GITHUB_TOKEN` is set, otherwise POSTs to `NUVEL_FEEDBACK_URL`, otherwise queues into `<feedback-dir>/feedback-queue.jsonl`.

**Exit codes:** `0` after a clean shutdown; `1` unreadable skills hub, invalid `--trace-store` / `--recipe-store`, or the HTTP port cannot be bound; `2` refused configuration: `--tools` names no known tool, a non-loopback `--host` without `--allow-remote`, `--no-auth` on stdio or a non-loopback host, `--no-auth` with `--require-auth`, an invalid `NUVEL_MCP_*` setting, missing OAuth configuration, or a database that cannot be opened.

```console
$ nuvel mcp serve --transport http --no-auth --port 8811
Storage: files under ~/.nuvel/feedback.
WARNING: --no-auth: OAuth is OFF, every MCP method and tool (writes included) is open to anything that can reach 127.0.0.1:8811.
nuvel-skills MCP server starting (skills_dir=none (feedback-only))
Listening on http://127.0.0.1:8811  (streamable: /mcp, legacy SSE: /sse, health: /healthz)

$ curl -s http://127.0.0.1:8811/healthz
{"status": "ok", "transport": "http", "storage": "files", "skills": 0, "themes": 0, "tools": 4}

$ nuvel mcp serve --transport http --host 0.0.0.0
Storage: files under ~/.nuvel/feedback.
Error: refusing to bind non-loopback address '0.0.0.0' without --allow-remote. The HTTP transport is meant to stay on 127.0.0.1 unless you deliberately expose it.
```

### `nuvel mcp client`

Manage the OAuth clients of the HTTP server. The store is the database when `NUVEL_MCP_DATABASE_URL` is set, else the JSON file at `$NUVEL_MCP_OAUTH_STORE` or `~/.nuvel/oauth.json`.

#### `nuvel mcp client create`

Issue a confidential client (client credentials grant; plus the authorization code flow when `--redirect-uri` is given), or, with `--url-key`, a client reached through a connector URL with no sign-in. The secret or key is printed once; only its hash is kept.

```bash
nuvel mcp client create --name NAME [--redirect-uri URI ...] [--url-key [--expires-in DAYS]] [--person P] [--company C]
```

| Flag | Description |
|---|---|
| `--name` | *(required)* Human-readable client name. |
| `--redirect-uri` | Allowed redirect URI (`https://`, or `http://` on loopback). Repeatable. |
| `--url-key` | Issue a URL key instead of a secret and print the connector URL (`$NUVEL_MCP_PUBLIC_URL/mcp/k/<key>`). Not combinable with `--redirect-uri`. |
| `--expires-in` | With `--url-key` only: the key stops working after this many days (fractions allowed). Default: until revoked. |
| `--person` | Who uses this client, saved as with `nuvel mcp clients label`. Needs `$NUVEL_MCP_DATABASE_URL`. |
| `--company` | Their company, saved the same way. |

**Exit codes:** `0` created; `1` the OAuth store cannot be read; `2` invalid flags (`--expires-in` without `--url-key`, `--url-key` with `--redirect-uri`, a bad redirect URI or expiry, `--person`/`--company` without a database).

```console
$ nuvel mcp client create --name ci-runner
{
  "client_id": "nvcl_ng3XI-MS_mAbl8GiDYLTcA",
  "client_secret": "nvcs_nekPKG…",
  "client_name": "ci-runner",
  "grant_types": [
    "client_credentials"
  ],
  "redirect_uris": [],
  "token_endpoint_auth_method": "client_secret_basic"
}
Store the client_secret now: only its hash is kept in ~/.nuvel/oauth.json.
```

#### `nuvel mcp client list`

List registered clients, without secrets.

```bash
nuvel mcp client list [--json]
```

| Flag | Description |
|---|---|
| `--json` | Print JSON. |

**Exit codes:** `0`, including when there are no clients; `1` the OAuth store cannot be read.

```console
$ nuvel mcp client list
CLIENT_ID                    NAME                     TYPE         SOURCE TOKENS  CREATED
nvcl_ng3XI-MS_mAbl8GiDYLTcA  ci-runner                confidential cli         0  2026-11-05 11:39
nvcl_y4dE1myUyspLrcY0FEEiMQ  tester                   url-key      cli         0  2026-11-05 11:39
```

#### `nuvel mcp client delete`

Delete a client and revoke all its tokens.

```bash
nuvel mcp client delete <client_id>
```

| Argument | Description |
|---|---|
| `<client_id>` | The client to delete. |

**Exit codes:** `0` deleted; `1` no such client, or the OAuth store cannot be read.

```console
$ nuvel mcp client delete nvcl_ng3XI-MS_mAbl8GiDYLTcA
Deleted client nvcl_ng3XI-MS_mAbl8GiDYLTcA and revoked its tokens.
```

### `nuvel mcp token`

Manage the OAuth tokens of the HTTP server.

#### `nuvel mcp token revoke`

Revoke an access or refresh token, a URL key (or the connector URL holding it), or every token and URL key of a client id.

```bash
nuvel mcp token revoke <TOKEN_KEY_OR_CLIENT_ID>
```

| Argument | Description |
|---|---|
| `<TOKEN_KEY_OR_CLIENT_ID>` | A token, URL key, connector URL or client id. |

**Exit codes:** `0` revoked; `1` nothing matches, or the OAuth store cannot be read.

```console
$ nuvel mcp token revoke nvcl_ng3XI-MS_mAbl8GiDYLTcA
Revoked 0 access token(s), 0 refresh token(s) and 0 URL key(s) (client, client nvcl_ng3XI-MS_mAbl8GiDYLTcA).
```

### `nuvel mcp db`

Manage the Postgres database named by `NUVEL_MCP_DATABASE_URL`. Needs the `psycopg` driver.

Shared **exit codes** of the `db` commands: `2` when `NUVEL_MCP_DATABASE_URL` is not set or invalid; `1` on a database error (printed as one line, password redacted).

```console
$ nuvel mcp db status
Error: NUVEL_MCP_DATABASE_URL is not set.
```

#### `nuvel mcp db migrate`

Apply pending schema migrations.

```bash
nuvel mcp db migrate
```

Takes no arguments.

**Exit codes:** `0` applied, or already up to date; see `nuvel mcp db` for `1` and `2`.

```console
$ nuvel mcp db migrate
Applied 0001_initial.
Applied 0002_row_level_security.
Applied 0003_usage_insights.
```

#### `nuvel mcp db status`

Show applied and pending migrations, and the row count of each table.

```bash
nuvel mcp db status [--json]
```

| Flag | Description |
|---|---|
| `--json` | Print JSON. |

**Exit codes:** `0` up to date; `1` migrations are pending (or a database error); `2` no database configured.

```console
$ nuvel mcp db status
Schema:   nuvel_mcp
Applied:  1, 2, 3
Pending:  -
  client_profiles        0
  oauth_access_tokens    0
  oauth_clients          0
  oauth_codes            0
  oauth_refresh_tokens   0
  oauth_url_keys         0
  recipe_signals         0
  recipes                0
  skill_runs             0
  usage_events           0
```

#### `nuvel mcp db import`

Copy the file stores (OAuth JSON, traces, recipes) into the database, migrating it first. Traces and recipes are deduplicated; OAuth is imported only while the database has no OAuth state.

```bash
nuvel mcp db import [--oauth-store PATH] [--feedback-dir DIR] [--force-oauth]
```

| Flag | Description |
|---|---|
| `--oauth-store` | OAuth JSON file. Default: `$NUVEL_MCP_OAUTH_STORE`, then `~/.nuvel/oauth.json`. |
| `--feedback-dir` | Feedback directory holding `_runs/` and `_recipes/`. Default: `$NUVEL_FEEDBACK_DIR`, then `~/.nuvel/feedback`. |
| `--force-oauth` | Import the OAuth file even though the database already has OAuth state. Clients and keys deleted in the database since come back. |

**Exit codes:** `0` imported; see `nuvel mcp db` for `1` and `2`.

```console
$ nuvel mcp db import
  oauth_clients          +1
  oauth_codes            +0
  oauth_access_tokens    +0
  oauth_refresh_tokens   +0
  oauth_url_keys         +0
  skill_runs             +0
  recipe_signals         +0
  recipes                +0
Imported 1 row(s); 0 unreadable record(s) skipped.
```

### `nuvel mcp clients`

Record who is behind each OAuth client id and list clients with their recent usage. Needs `NUVEL_MCP_DATABASE_URL`. What the usage log holds is listed in [Usage insights](../how-to/deploy-skills-mcp.md#usage-insights-what-is-collected).

#### `nuvel mcp clients label`

Set the person, company or notes of a client id. An empty value clears that field; omitted fields are kept. A client id with no client and no usage yet is labeled anyway, with a note.

```bash
nuvel mcp clients label <client_id> [--person P] [--company C] [--notes N]
```

| Argument / flag | Description |
|---|---|
| `<client_id>` | The client to label. |
| `--person` | Who uses this client. |
| `--company` | Their company. |
| `--notes` | Free-form operator notes. |

**Exit codes:** `0` labeled; `1` database error; `2` invalid value, or no database configured.

```console
$ nuvel mcp clients label nvcl_ng3XI-MS_mAbl8GiDYLTcA --person "Ana Ruiz" --company Acme
Labeled nvcl_ng3XI-MS_mAbl8GiDYLTcA.
```

#### `nuvel mcp clients list`

Every known client id with its label, last usage and events in the last 30 days.

```bash
nuvel mcp clients list [--json]
```

| Flag | Description |
|---|---|
| `--json` | Print JSON. |

**Exit codes:** `0`, including when there are no clients; `1` database error; `2` no database configured.

```console
$ nuvel mcp clients list
CLIENT_ID                    NAME       PERSON    COMPANY  LAST_SEEN  EVENTS_30D
nvcl_ng3XI-MS_mAbl8GiDYLTcA  ci-runner  Ana Ruiz  Acme             -           0
```

### `nuvel mcp report`

The usage report from the database: active clients, calls, tools, errors, latency, client apps, countries, skill runs and recipe adoption. Needs `NUVEL_MCP_DATABASE_URL`.

```bash
nuvel mcp report [--days N] [--json]
```

| Flag | Default | Description |
|---|---|---|
| `--days` | `7` | Window in days, ending now; 1 to 366. |
| `--json` | off | Print JSON. |

**Exit codes:** `0`; `1` database error; `2` `--days` out of range, or no database configured.

```console
$ nuvel mcp report --days 7
Usage report: last 7 day(s), 2026-10-29T11:39 to 2026-11-05T11:39 UTC
0 call(s), 0 error(s) (0.0%), 0 active client(s).
No usage events in this window.

Active clients
  (none)

Calls per day
  (none)

Methods
  (none)

Tools
  (none)

Client apps
  (none)

Countries
  (no country recorded; see NUVEL_MCP_GEOIP_CSV)

Skill runs
  (none)

Tools never called
  check_skill_health, fence_value, get_skill, install_skill, list_skills, list_themes, propose_improvement, record_feedback, record_recipe_signal, record_run_trace, search_skills, share_recipe

Recipe adoption
  (none)
```

## `nuvel dashboard`

Open the local web dashboard over your trace logs. It serves until interrupted. Needs `fastapi` and `uvicorn`; without them the command is not registered.

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

**Exit codes:** `0` after shutdown; `1` the port is in use.

```console
$ nuvel dashboard --no-open --port 8799
nuvel dashboard -> http://127.0.0.1:8799
```

## `nuvel eval`

Online trace scorer. Heuristics-first, judge-on-pass. Writes `scored.jsonl` siblings into each trace directory; the dashboard joins on `trace_id` and surfaces a score column. Without the eval dependencies the command is not registered.

### `nuvel eval score`

Run heuristics on every discovered trace; call the LLM judge on runs that survive the heuristic floor (so `no_assistant_output` / `incomplete_trace` skip the judge for free). Idempotent — runs with a matching `trace_id` + `scorer_version` are skipped unless `--force` is given.

```bash
nuvel eval score [-s <dir>] [--max-cost-usd 1.00] [--concurrency 5] [--force] [--dry-run]
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | discover | Extra trace directory to scan. Repeatable. |
| `--max-cost-usd` | `1.00` | Hard cap on judge spend. Once crossed, remaining runs score with heuristics only. |
| `--concurrency` | `5` | Max simultaneous judge calls. |
| `--force` | off | Rescore every run regardless of existing `scored.jsonl` rows. |
| `--dry-run` | off | Compute scores but write nothing. |

Resolves the judge model in this order: per-agent `generated-agents/<agent>/evals/rubric.yaml` `judge.model` → `EVAL_JUDGE_MODEL` env var → `DEFAULT_FAST_MODEL`. Calls go through `litellm.acompletion`, so any provider-prefixed id works. A failed judge call is logged and counted; the run keeps its heuristic score.

**Exit codes:** `0`, including when judge calls fail.

```console
$ nuvel eval score
Scored: 3
  skipped (already scored at current version): 0
  judge skipped (heuristic floor / disabled): 0
  judge errors: 3
  total judge cost: $0.0000

Per directory:
     3  ~/work/traces
```

(Run with no API key: each of the three judge calls failed, so each run kept its heuristic score.)

### `nuvel eval report`

Per-agent summary table: run count, mean overall, mean per component, most common flag.

```bash
nuvel eval report [--agent <q>] [--since <date>] [-s <dir>]
```

| Flag | Description |
|---|---|
| `--source`, `-s` | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | Agent name substring. |
| `--since` | Only scored rows at or after this date (`YYYY-MM-DD` or ISO; no offset means UTC). |

**Exit codes:** `0`, including when nothing is scored (`No scored runs.`).

```console
$ nuvel eval report
AGENT    RUNS  OVERALL  SUCC  QUAL  EFFI  RELI  TOP FLAG
-------  ----  -------  ----  ----  ----  ----  ----------------
(local)  3     0.62     1.00  0.00  0.83  0.67  cost_outlier (1)
```

### `nuvel eval worst`

The N worst-scoring runs ranked by `overall`, with judge notes inline. The triage entry point: start your day with `nuvel eval worst -n 10`.

```bash
nuvel eval worst [-n 10] [--agent <q>] [--since <date>] [-s <dir>]
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | discover | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | all | Agent name substring. |
| `--since` | all | Only scored rows at or after this date. |
| `-n` | `10` | Max rows. |

**Exit codes:** `0`, including when nothing is scored.

```console
$ nuvel eval worst -n 2
SCORE  AGENT                    TRACE_ID       FLAGS / NOTE
-----------------------------------------------------------
0.55   (local)                  7ae5f4645a1e47 tool_error,llm_error
0.62   (local)                  c3d4e5f6a7b8c9 cost_outlier,latency_outlier,token_bloat
```

### `nuvel eval drift`

Rolling-window comparison per agent. Computes mean `overall` for the last `--window-days` and compares it against the same-length window before it.

```bash
nuvel eval drift [--window-days 7] [--threshold 0.1] [--agent <q>] [-s <dir>]
```

| Flag | Default | Description |
|---|---|---|
| `--source`, `-s` | discover | Extra trace directory to scan. Repeatable. |
| `--agent`, `-a` | all | Agent name substring. |
| `--window-days` | `7` | Length of each rolling window. |
| `--threshold` | `0.1` | Flag drift when `|delta| >= threshold`. |

**Exit codes:** `0` no drift; `2` any agent crosses `--threshold`, so a CI or webhook layer can react without parsing.

```console
$ nuvel eval drift
AGENT    CURRENT  BASELINE  DELTA  CUR_N  BASE_N  STATUS
-------  -------  --------  -----  -----  ------  ------------
(local)  0.62     -         -      3      0       insufficient
```

## `nuvel bots`

Manage and talk to Hermes-backed bots: each bot is a Hermes profile, driven through the `hermes` executable. Needs the Hermes runtime installed.

Every `bots` command takes these flags:

| Flag | Description |
|---|---|
| `--hermes-bin` | Path to the `hermes` executable. Default: found on `PATH`. |
| `--json` | Emit machine-readable JSON on stdout. |
| `--verbose`, `-v` | Show the full traceback on error. |

Shared **exit codes** of the `bots` commands: `0` on success; `1` when the bot does not exist or `hermes` fails, with the error on stderr.

### `nuvel bots list`

List all bots with their default model.

```bash
nuvel bots list [--hermes-bin PATH] [--json] [-v]
```

Takes only the shared flags: `--hermes-bin`, `--json`, `--verbose`, `-v`.

**Exit codes:** `0`; `1` `hermes` fails.

```console
$ nuvel bots list
  default   —
  demo-bot  —
```

### `nuvel bots create`

Create a bot, optionally cloned from another and with hub skills installed.

```bash
nuvel bots create <name> [--model M] [--description D] [--clone-from BOT] [--skills a/b,c/d] [--list-skills] [--hub DIR] [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Bot name: lowercase alphanumerics, `-` or `_`. |
| `--model` | Default model for the bot. |
| `--description` | Bot description. |
| `--clone-from` | Seed config and skills from an existing bot. |
| `--skills` | Comma-separated skill refs to install, e.g. `hr/payroll,customer/triage`. |
| `--list-skills` | List the hub's skills and exit without creating anything. |
| `--hub` | Path to a local skills-hub checkout. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` created (or listed); `1` `hermes` fails.

```console
$ nuvel bots create demo-bot --description "Answers billing questions"
Created bot 'demo-bot'.
```

### `nuvel bots delete`

Delete a bot.

```bash
nuvel bots delete <name> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Bot to delete. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` deleted; `1` no such bot or `hermes` fails.

```console
$ nuvel bots delete demo-bot
Deleted bot 'demo-bot'.
```

### `nuvel bots chat`

Send one message to a bot and print its reply. The bot needs a model and its provider key.

```bash
nuvel bots chat <name> <message> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Bot to talk to. |
| `<message>` | Message to send. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` replied; `1` no such bot or `hermes` fails.

```console
$ nuvel bots chat demo-bot "hello"
Error: hermes -p demo-bot chat -Q -q hello failed:
```

(Run on a bot with no model or provider key configured, so `hermes` failed.)

### `nuvel bots info`

Show a bot's name, model and description.

```bash
nuvel bots info <name> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Bot to inspect. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0`; `1` no such bot or `hermes` fails.

```console
$ nuvel bots info demo-bot
name:        demo-bot
model:       —
description: Answers billing questions
```

### `nuvel bots logs`

Show a bot's most recent log lines.

```bash
nuvel bots logs <name> [-n N] [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<name>` | *(required)* | Bot whose logs to show. |
| `--limit`, `-n` | `10` | Number of log lines. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | | Shared flags. |

**Exit codes:** `0`; `1` no such bot or `hermes` fails.

```console
$ nuvel bots logs demo-bot -n 3
--- ~/.hermes/profiles/demo-bot/logs/agent.log (last 3) ---
```

### `nuvel bots send`

Deliver a message from one bot to another, in the receiving bot's `Agent Inbox` session, and print the reply.

```bash
nuvel bots send <from> <to> <message> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<from>` | Sending bot. |
| `<to>` | Receiving bot. |
| `<message>` | Message to deliver. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` delivered; `1` no such bot or `hermes` fails.

```console
$ nuvel bots send triage-bot billing-bot "Customer 42 asks about a refund."
Error: hermes -p billing-bot chat -c 'Agent Inbox' -Q -q 'Message from triage-bot: Customer 42 asks about a refund.' failed: No session found matching 'Agent Inbox'.
Use 'hermes sessions list' to see available sessions, or pass --create-if-missing to start a new session with that title.
```

(Run against a receiving bot with no `Agent Inbox` session yet.)

### `nuvel bots skills`

Browse the Nuvel skills hub that `nuvel bots create --skills` installs from.

#### `nuvel bots skills list`

List the hub's skills, grouped by category.

```bash
nuvel bots skills list [--category C] [--hub DIR] [--hermes-bin PATH] [--json] [-v]
```

| Flag | Description |
|---|---|
| `--category` | Only show skills in this category. |
| `--hub` | Path to a local skills-hub checkout. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0`, including when there are no skills (`No skills found.`).

```console
$ nuvel bots skills list --hub ./skills
summarize
  summarize  Summarize a document in three bullets.
```

#### `nuvel bots skills search`

Search the hub's skills by name, description or tag.

```bash
nuvel bots skills search <query> [--hub DIR] [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<query>` | Search text. |
| `--hub` | Path to a local skills-hub checkout. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0`, including when nothing matches.

```console
$ nuvel bots skills search payroll
No skills found.
```

## `nuvel fleet`

Deploy and manage fleets of bots from one YAML manifest. A deploy records the fleet in `~/.hermes/fleets/<name>.json`. The manifest keys are `name`, `company`, `hub`, `default_model`, `bots`, `vision` and `manager`; each bot takes `name`, `role`, `description`, `model` and `skills`:

```yaml
name: acme-support
company: Acme Corp
bots:
  - name: triage-bot
    description: Classifies and routes incoming customer requests
  - name: billing-bot
    description: Answers billing questions
```

Every `fleet` command takes the shared `bots` flags `--hermes-bin`, `--json` and `--verbose` / `-v`, and exits `1` when `hermes` fails or the manifest is invalid.

### `nuvel fleet deploy`

Create every bot in a manifest and install its skills. A bot that already exists is left alone; a bot that fails is reported and does not stop the rest.

```bash
nuvel fleet deploy <manifest> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<manifest>` | Path to the fleet manifest (YAML). |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` every bot deployed; `1` any bot failed, or the manifest is invalid.

```console
$ nuvel fleet deploy fleet.yaml
Fleet 'acme-support': ok
  triage-bot: created
  billing-bot: created
```

### `nuvel fleet list`

List the deployed fleets.

```bash
nuvel fleet list [--hermes-bin PATH] [--json] [-v]
```

Takes only the shared flags: `--hermes-bin`, `--json`, `--verbose`, `-v`.

**Exit codes:** `0`, including when there are none (`No fleets deployed.`).

```console
$ nuvel fleet list
acme-support
```

### `nuvel fleet status`

Show a fleet's bots and their deploy status, plus its manager, vision and routines when set.

```bash
nuvel fleet status <name> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Fleet name. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0`; `1` no fleet by that name.

```console
$ nuvel fleet status acme-support
Fleet 'acme-support' (Acme Corp)
  triage-bot: created
  billing-bot: created
  vision:  ~/.hermes/fleets/acme-support/VISION.md
```

### `nuvel fleet destroy`

Delete every bot in a manifest and its fleet record.

```bash
nuvel fleet destroy <manifest> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<manifest>` | Path to the fleet manifest (YAML). |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` destroyed; `1` `hermes` fails or the manifest is invalid.

```console
$ nuvel fleet destroy fleet.yaml
Fleet destroyed.
```

### `nuvel fleet update-vision`

Replace a fleet's `VISION.md`, its constitution.

```bash
nuvel fleet update-vision <name> <source> [--hermes-bin PATH] [--json] [-v]
```

| Argument / flag | Description |
|---|---|
| `<name>` | Fleet name. |
| `<source>` | Inline markdown (starting with `#` or `---`) or a path to a file. |
| `--hermes-bin`, `--json`, `--verbose`, `-v` | Shared flags. |

**Exit codes:** `0` updated; `1` no such fleet, or the source cannot be read.

```console
$ nuvel fleet update-vision acme-support "# Acme support: answer within one business day."
Updated VISION.md for fleet 'acme-support'.
```

## `nuvel evalv2`

Skill-driven evaluation: a skill ships an `eval/` suite (`suite.yaml` plus `examples/`), and runs are saved under the evalv2 results directory so they can be compared against a baseline. Every subcommand takes `--skills-dir`, the skills directory `<skill>` is resolved against (default: the configured skills directory).

### `nuvel evalv2 init`

Create an `eval/` suite in a skill: a `suite.yaml` with an LLM judge and a length check, and an empty `examples/` directory.

```bash
nuvel evalv2 init <skill> [--skills-dir DIR] [--name NAME] [--description TEXT] [--force]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<skill>` | *(required)* | Skill name under the skills directory, or a path. |
| `--skills-dir` | configured | Skills directory. |
| `--name` | `<skill>-eval` | Suite name. |
| `--description` | `""` | One-line suite description. |
| `--force` | off | Overwrite an existing `eval/` directory. |

**Exit codes:** `0` created; `1` the skill directory does not exist, or it already has `eval/` and `--force` was not given.

```console
$ nuvel evalv2 init summarize --skills-dir ./skills
Initialized eval suite in skills/summarize
  skills/summarize/eval/suite.yaml
  skills/summarize/eval/examples/

Add examples under skills/summarize/eval/examples/, then run `nuvel evalv2 run summarize`.
```

### `nuvel evalv2 list`

List the skills that ship an `eval/` suite, with the suite name, example count and evaluators.

```bash
nuvel evalv2 list [--skills-dir DIR]
```

| Flag | Description |
|---|---|
| `--skills-dir` | Skills directory. |

**Exit codes:** `0`, including when there are none.

```console
$ nuvel evalv2 list --skills-dir ./skills
  summarize  summarize-eval  (0 examples; llm-judge,deterministic)
```

### `nuvel evalv2 run`

Run a skill's suite, print the per-example scores and save the run.

```bash
nuvel evalv2 run <skill> [--skills-dir DIR] [--model M] [--json] [--save-baseline]
```

| Argument / flag | Description |
|---|---|
| `<skill>` | Skill name (directory under the skills directory). |
| `--skills-dir` | Skills directory. |
| `--model` | Model override for the run. |
| `--json` | Emit the result as JSON on stdout. |
| `--save-baseline` | Also save this run as the baseline `nuvel evalv2 compare` diffs against. |

**Exit codes:** `0` the run completed; `1` the skill has no `eval/suite.yaml`, the suite is invalid, the run failed, or the result cannot be written.

```console
$ nuvel evalv2 run summarize --skills-dir ./skills --save-baseline
suite: summarize-eval  (skill: summarize)
model: default

id                             score  verdict
------------------------------------  --------

overall:   —     pass=0 warn=0 fail=0 unscored=0 (total 0)
```

### `nuvel evalv2 compare`

Diff a skill's latest run against its baseline.

```bash
nuvel evalv2 compare <skill> [--skills-dir DIR] [--json] [--threshold T]
```

| Argument / flag | Default | Description |
|---|---|---|
| `<skill>` | *(required)* | Skill name (directory under the skills directory). |
| `--skills-dir` | configured | Skills directory. |
| `--json` | off | Emit the comparison as JSON on stdout. |
| `--threshold` | `-0.05` | Regression threshold on the overall delta. |

**Exit codes:** `0` no regression; `2` regression, so CI can fail on it; `1` no baseline or no run recorded yet.

```console
$ nuvel evalv2 compare summarize
baseline: 2026-11-05T11:40:05.295340+00:00
current:  2026-11-05T11:40:05.295340+00:00

id                              base    curr    delta  verdict
-----------------------------------------------------  --------

overall delta: —   wins=0 losses=0 ties=0
warning: no overlapping example ids — nothing to compare
no regression
```

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

The `hermes` and `claude_agent_sdk` backends also export `scaffold_agent()` and declare these keyword arguments. Those they do not support return `{"status": "error", "message": ...}` if set; see [Pick a backend](../how-to/pick-a-backend.md#nuvel-feature-support) for which those are. `with_litellm` is declared only by the ADK backend, so passing it to the other two raises `TypeError`. `nuvel agent create` inspects each signature first, so on the command line that case is a normal error naming the flag and the backend. Hermes also takes `model` and `telegram_bot_token`.
