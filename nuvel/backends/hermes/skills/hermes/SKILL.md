---
name: hermes
description: Building and operating Hermes agents — the SOUL.md / config.yaml / skills profile shape, model and turn-budget config, installing a profile into a Hermes home, and the Telegram gateway with its DM policy. Read when scaffolding with `--framework hermes`, when a Hermes bot needs to answer on Telegram, when deciding what belongs in SOUL.md versus config.yaml, or when a profile isn't loading.
---

# Hermes agents

A Hermes agent is not a program you run. It is a **profile directory** the
Hermes runtime loads:

```
<hermes_home>/profiles/<name>/
├── SOUL.md        identity — loaded every turn
├── config.yaml    model, max_turns, platforms, enabled skills
├── .env.example   the secrets the profile needs (copy to .env / export)
└── skills/<slug>/SKILL.md
```

`nuvel new <name> --framework hermes` stamps exactly that. Installing it means
moving the stamped package directory into `<hermes_home>/profiles/<name>/`
(default home `~/.hermes`, overridable with `HERMES_HOME`) — the same layout
`nuvel bots` manages through the `hermes` CLI.

There is no server, no model loop, and no requirements.txt in a Hermes agent.
If you find yourself writing one, you're building an ADK or Buzz agent and
should switch frameworks rather than fight this one.

## SOUL.md vs config.yaml

The split is the thing most worth internalizing, because getting it wrong is
what makes a profile impossible to tune later:

| | SOUL.md | config.yaml |
|---|---|---|
| Holds | who the agent is, what it's for, how it speaks | which model, how many turns, which platforms |
| Changing it | changes behaviour | changes cost, reach, or latency |
| Audience | the model, every single turn | the runtime, at load |

`model.default` never belongs in SOUL.md, and "be concise in Telegram" never
belongs in config.yaml. SOUL.md is loaded on every turn — keep it short. A
600-line SOUL.md is a per-turn tax on every conversation the agent ever has.

## Configuration

```yaml
model:
  default: deepseek/deepseek-v4-flash   # bare id — no LiteLLM provider prefix
  provider: openrouter
agent:
  max_turns: 60
platforms:
  telegram:
    enabled: false
skills:
  enabled: []
```

Two things that trip people up:

- **The model id is bare.** `provider` selects the endpoint, so
  `openrouter/deepseek/deepseek-v4-flash` is wrong here even though that's the
  form nuvel's ADK backend uses for LiteLLM routing. Strip the prefix.
- **`skills.enabled: []` does not mean "no skills."** It's a force-load list.
  Empty is the normal state: every skill under `skills/` stays discoverable by
  description, and bodies load on demand. Name a slug there only when the
  skill must be in context from the first token — each one is a permanent
  prompt cost.

`max_turns` bounds model↔tool round trips per turn. If you see it hit in logs,
the agent is stuck in a loop, not working hard.

Anything set here has a `hermes config set <key> <value>` equivalent
(`model.default`, …). The file is the checked-in starting point; the CLI is
how you change one knob on a running install without editing YAML.

## Skills

Anthropic format: `skills/<slug>/SKILL.md`, YAML frontmatter with `name` and
`description`, then the body. Only frontmatter is loaded up front — that's
what makes fifty skills affordable.

Write the `description` for a reader deciding *whether to open the file*: name
the situation ("Read when an alert fires"), not the topic ("about incidents").
It's the only signal available at selection time.

Skills are shared across the nuvel ecosystem — `nuvel bots skills list` browses
the hub and `nuvel bots create --skills <refs>` installs them straight into
`<hermes_home>/profiles/<bot>/skills/`. A skill written for a Hermes profile
drops into Claude Code or an ADK agent unchanged, as long as it doesn't hard-
code a harness-specific tool name.

## Telegram

`nuvel new <name> --framework hermes --with-telegram` swaps in a config.yaml
with the platform on:

```yaml
platforms:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    dm_policy: allowlist
    allowed_users: []
    mention_only: true
```

- **`bot_token` stays an env reference.** The token *is* the bot — anyone
  holding it can post as the agent. It belongs in `.env` or a secret manager,
  never in a committed config.
- **`dm_policy: allowlist` is the safe default.** `open` means anyone who
  finds the bot can spend your model budget. Start restricted; widen once you
  know what traffic looks like.
- **`mention_only: true` in groups.** A bot answering every message in a busy
  group is expensive and socially exhausting — the same lesson the Buzz
  backend encodes as `BUZZ_REPLY_POLICY=mention`.

Slack and Teams are ADK-only. `--with-slack` / `--with-teams` are rejected here
rather than silently ignored, as are `--persona` (a Hermes profile already has
a SOUL.md), `--with-composio`, `--workflow`, `--with-eval`, and `--with-acp`
(Hermes owns the runtime, so there's no process to hand an ACP adapter to —
use `--framework buzz` if ACP is what you want).

## Debugging

| Symptom | Look at |
|---|---|
| Profile doesn't appear in `hermes profile list` | it's not under `<hermes_home>/profiles/<name>/`; check `HERMES_HOME` |
| Model calls fail with an auth error | the key env for `model.provider` isn't exported into Hermes' environment |
| "unknown model" from the provider | a provider prefix left on `model.default` |
| Telegram bot silent in a group | `mention_only: true` and nobody @-mentioned it |
| Telegram bot silent in DMs | `dm_policy: allowlist` and the sender's numeric id isn't in `allowed_users` |
| Agent ignores a skill it should have used | the `description` names a topic, not a situation — rewrite it for the selection moment |
