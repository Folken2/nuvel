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

## Voice memos

When `GATEWAY_TRANSCRIBE_AUDIO=1`, voice notes on Slack and Telegram are transcribed (Whisper via OpenAI or Groq) before reaching the agent — the audio attachment is replaced with `[Voice memo, M:SS]: <transcript>` and forwarded as a normal text turn. Full env-var reference: [Voice transcription](../reference/env-vars.md#voice-transcription-slack-telegram).
