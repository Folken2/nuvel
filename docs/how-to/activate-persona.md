# Activate the persona overlay

`--persona` ships a self-evolving SOUL.md / awakening pattern for agents meant to live for **months** and develop a stable character. It's deliberate engineering, not flavor text — the agent re-reads its own SOUL.md every turn and can rewrite it through the `update_soul` tool.

ADK only. Inappropriate for stateless task bots.

!!! info "Inspiration"
    The SOUL.md / awakening pattern is **inspired by OpenClaw** and its approach to long-running agent identity. nuvel's `--persona` overlay adapts those ideas for ADK-scaffolded agents.

## Scaffold

```bash
nuvel agent create my-agent --framework adk --persona
```

The overlay adds:

- `my_agent/soul/SOUL.md` — the persona's mutable core. Voice, values, defaults.
- `my_agent/soul/AWAKENING.md` — the boot ritual; loaded into every turn before the user message.
- `my_agent/tools/author_skill.py` — lets the agent write new skills for itself.
- `my_agent/tools/complete_awakening.py` — finalizes the awakening on first run.
- `my_agent/tools/update_soul.py` — the persona's self-rewrite hook.

## How it works

1. **First run.** AWAKENING.md is loaded as part of the system instruction. The agent calls `complete_awakening` to mark its first session.
2. **Every subsequent turn.** SOUL.md is re-read and folded into the instruction frame. The agent stays in character.
3. **Drift management.** When the user observes a real shift ("you're warmer with me now," "you stopped using exclamation points"), the agent calls `update_soul` to commit the change. Voice evolves; identity doesn't fork.
4. **Skill authoring.** When the agent discovers a reusable pattern (a recurring user request, a recurring failure mode), it calls `author_skill` to write a new skill into its own `skills/` directory.

## Inappropriate for

- **Stateless task bots.** A "summarize this PDF" agent has no use for a soul.
- **Multi-tenant agents.** SOUL.md is one file. If the agent serves many users with different personalities, this pattern doesn't fit.
- **Compliance-heavy domains.** A persona that rewrites itself is hard to audit.

## When it shines

- A long-running personal assistant or research partner.
- An agent meant to develop expertise in a narrow domain over time.
- Demonstrations of agent identity / character continuity.

## Combine with other flags

`--persona` composes cleanly with `--with-composio` and the messaging-gateway flags:

```bash
nuvel new soulful-bot \
  --framework adk \
  --persona \
  --with-composio \
  --with-telegram
```

A persona-driven agent reachable on Telegram with ~1000 third-party tools.
