# Add skills to an agent

Skills are nuvel's progressive-disclosure knowledge primitive. nuvel bundles a separate set per backend:

| Backend | Bundled skills | Covers |
|---|---|---|
| Google ADK | 15 `adk-*` | Agent patterns, tools, prompts, callbacks, streaming, workflow graphs, the Composio Tool Router, memory, and more. |
| Claude Agent SDK | 6 `claude-sdk-*` | Tool creation, hooks, permissions, MCP integration, system prompt, deployment. |
| Hermes Agent | 1 `hermes` | The profile shape, model and turn-budget config, the Telegram gateway. |

## List what's bundled

```bash
nuvel skills list --framework adk
nuvel skills list --framework claude-agent-sdk
nuvel skills list --framework hermes
```

Each entry shows the slug, name, and a one-line description.

## Search

```bash
nuvel skills search slack --framework adk
nuvel skills search "tool router" --framework adk
```

Substring match against slug, name, and description (case-insensitive).

## Where they live in a generated agent

The bundled skills above are knowledge for whoever is *building* the agent, so they stay in nuvel and aren't stamped into the project. A generated agent has its own skills directory for the skills it uses at runtime:

| Backend | Directory | At scaffold time |
|---|---|---|
| Google ADK | `my_agent/skills/` | Empty, unless `--with-eval` adds a `default` skill. |
| Claude Agent SDK | `.claude/skills/` | Empty. |
| Hermes Agent | `my_agent/skills/` | One starter skill, `getting-started`. |

Each skill is a directory with a `SKILL.md` (frontmatter + prose) plus optional helper files. Only `description` fields load at start; the full body is fetched on-demand when the skill becomes relevant.

## Adding your own skill to a generated agent

1. Create `<skills directory>/<slug>/SKILL.md` with frontmatter:

   ```markdown
   ---
   name: <slug>
   description: <one-line description for trigger detection>
   ---

   # <Title>

   <Body — instructions, examples, edge cases>
   ```

2. Restart the agent. The skill index rebuilds at startup.

That's it. There's no registration step — skills are discovered by walking the `skills/` directory.

## Adding a skill to nuvel itself

If you want a skill listed by `nuvel skills list` for a given backend, drop it under `nuvel/backends/<framework>/skills/<slug>/` in the nuvel repo and open a PR. This doesn't add it to newly-scaffolded agents. See [Contributing](../contributing.md).
