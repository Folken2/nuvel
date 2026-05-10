# Use slash commands

Every gateway-enabled agent (`--with-slack`, `--with-telegram`, `--with-teams`) ships with a unified slash-command surface. The same commands work the same way on every channel — typed as the first word of a message.

## Built-in commands

| Command | Aliases | What it does |
|---|---|---|
| `/help` | — | Lists every registered command for the current agent. |
| `/new` | `/reset` | Clears the session — fresh conversation memory. |
| `/usage` | — | Turn count and a token-cost estimate for the current session. |
| `/stop` | — | Cooperatively cancels the running turn (tools that check `get_cancel_event(session_id)` exit early). |
| `/personality` | `/persona` | Switch a runtime personality overlay. See below. |
| `/cron` | — | Schedule, list, pause, resume, run, or remove jobs. See below. |

Anything that isn't a registered command is forwarded to the agent unchanged.

## Personalities

Personalities are runtime system-prompt overlays — lighter than `--persona`/SOUL.md, switchable per session, no rebuild needed. The two systems coexist; SOUL.md is baked at scaffold time, personalities flip at runtime.

### Storage

Drop markdown files at `~/.nuvel/personalities/<name>.md`:

```markdown
---
name: concise
description: Short, direct answers — no preamble, no filler.
---

You are a concise assistant. Answer in as few words as possible.
Skip greetings, qualifiers, and meta-commentary. Get to the point.
```

YAML frontmatter is optional. The body is what gets prepended to each user turn while the personality is active.

### Usage

```
> /personality
Available personalities:
  concise — Short, direct answers — no preamble, no filler.
  socratic — Teaches by asking questions before giving answers.
No personality is active. Use /personality <name>.

> /personality concise
Personality set to 'concise'.

> /personality off
Personality cleared.
```

### Examples shipped

The gateway-base overlay includes three example personalities (`concise`, `socratic`, `pirate`) under `<agent_package>/gateways/personalities_examples/`. Copy any you want to use into `~/.nuvel/personalities/`.

## Adding your own command

The registry lives at `<agent_package>/gateways/commands.py`. Register with the `@command` decorator:

```python
from .commands import command, CommandContext, CommandResult

@command("/insights", help="Show this week's insights")
async def cmd_insights(ctx: CommandContext) -> CommandResult:
    return CommandResult(handled=True, replies=["Top 3 things this week: ..."])
```

`CommandContext` carries `user_id`, `channel`, `session_id`, the raw text, and a `reply(text)` callable. `CommandResult` is `{handled: bool, replies: list[str]}`. New commands work on every channel automatically — no per-gateway code.

## Cron / scheduled jobs

The `/cron` command lets you schedule recurring or one-shot agent runs. The scheduler is opt-in — set `NUVEL_CRON_ENABLED=1` on the agent service. See [Cron env vars](../reference/env-vars.md#cron-scheduled-jobs-opt-in) for tuning, and [Deploy to Railway → cron](deploy-to-railway.md#cron-scheduled-jobs-on-railway) before shipping.

### Subcommands

```
/cron list
/cron add "<schedule>" "<prompt>" [--name <n>] [--deliver origin|local|slack:<ch>|telegram:<chat_id>]
/cron pause <id>
/cron resume <id>
/cron run <id>
/cron remove <id>
```

### Schedule formats

| Format | Example | Behavior |
|---|---|---|
| Relative one-shot | `30m`, `2h`, `1d` | Runs once after that delay. |
| Interval recurring | `every 30m`, `every 2h`, `every 1d` | Runs forever until removed. |
| Cron expression | `0 9 * * *`, `0 */6 * * *` | Standard 5-field cron. Runs forever. |
| ISO timestamp | `2026-12-15T09:00:00` | One-shot at that absolute time (UTC if no offset). |

### Delivery options

| Token | What it does |
|---|---|
| `origin` *(default on a gateway)* | Deliver back to the channel where `/cron add` was sent. |
| `local` | Write the output file only — no message. Useful for log-only jobs. |
| `slack:<channel>` | Send to a specific Slack channel. |
| `telegram:<chat_id>` | Send to a specific Telegram chat. |

### Silent suppression

If the agent's response starts with `[SILENT]`, delivery is suppressed (the output file is still written). Useful for monitoring jobs that should only ping when something is wrong:

```
/cron add "every 5m" "Check if our health endpoint returns 200. Reply [SILENT] if healthy, otherwise describe the failure." --name health-watch
```

### From chat (without typing /cron)

Because `cronjob` is a registered tool, you can also just ask:

> *"Every morning at 9am, summarize my unread emails and post to #standup."*

The agent will call `cronjob(action="create", ...)` itself. The `/cron` command is the explicit-control path; natural language is the conversational path. Both write to the same job store.

### Recursion guard

A cron-spawned agent run cannot create or modify other cron jobs. This is enforced via an internal `NUVEL_CRON_RUNNING` env flag — the `cronjob` tool refuses mutations when it's set. Read-only `list`/`get` still work.

## Voice memos

When `GATEWAY_TRANSCRIBE_AUDIO=1`, voice notes on Slack and Telegram are transcribed (Whisper via OpenAI or Groq) before reaching the agent — the audio attachment is replaced with `[Voice memo, M:SS]: <transcript>` and forwarded as a normal text turn. Full env-var reference: [Voice transcription](../reference/env-vars.md#voice-transcription-slack-telegram).
