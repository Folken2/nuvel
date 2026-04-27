# Agent Memory

Long-term memory for {{agent_name}}. The agent reads this file at the start
of every conversation to recall important context from prior sessions.

The agent can update this file using the `save_memory` and `update_memory` tools.
You can also edit this file directly to add or correct information.

## How It Works

- **Core memory** (this file): Always loaded into the agent's system prompt.
  Keep it concise — key facts, preferences, and learnings only.
- **Topic files** (`topics/*.md`): For detailed, topic-specific memory.
  Loaded on demand via the `recall_memory` tool.

## Learnings
