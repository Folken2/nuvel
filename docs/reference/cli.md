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
