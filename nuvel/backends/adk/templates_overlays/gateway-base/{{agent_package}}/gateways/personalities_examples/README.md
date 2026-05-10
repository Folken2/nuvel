# Personality Examples

Lightweight runtime overlays. Cousin of the heavier `--persona` build-time
SOUL.md system: personalities are plain markdown files toggled at runtime via
the `/personality` slash command, no rebuild required.

## How to use

1. Copy any example file into `~/.nuvel/personalities/`:
   ```
   mkdir -p ~/.nuvel/personalities
   cp gateways/personalities_examples/concise.md ~/.nuvel/personalities/
   ```
2. From any gateway (CLI / Slack / Telegram / Teams), run:
   ```
   /personality            # list available + show active
   /personality concise    # activate "concise" for this session
   /personality off        # clear the overlay
   ```

The active personality is per-session (in-memory). Restart clears it.

## File format

Plain markdown. Optional YAML frontmatter for a friendly description:

```markdown
---
name: concise
description: Short answers, no fluff.
---

You are an assistant who answers in two sentences max...
```
