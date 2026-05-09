# Add skills to an agent

Skills are nuvel's progressive-disclosure knowledge primitive. The bundled skills cover web search, file tools, memory, the Composio Tool Router, ADK callbacks, prompt engineering, deployment, persona authoring, and more.

## List what's bundled

```bash
nuvel skills list --framework adk
nuvel skills list --framework claude-agent-sdk
nuvel skills list --framework anthropic-managed-agents
```

Each entry shows the slug, name, and a one-line description.

## Search

```bash
nuvel skills search slack --framework adk
nuvel skills search "tool router" --framework adk
```

Substring match against slug, name, and description (case-insensitive).

## Where they live in a generated agent

When you scaffold an agent, the relevant skills are stamped into:

```
my-agent/
└── my_agent/
    └── skills/
        ├── ai-sdk/
        ├── composio-tool-router/
        ├── nuvel/
        └── ...
```

Each skill is a directory with a `SKILL.md` (frontmatter + prose) plus optional helper files. The agent's instruction frame loads only `description` fields at start; the full body is fetched on-demand when the skill becomes relevant.

## Adding your own skill to a generated agent

1. Create `my_agent/skills/<slug>/SKILL.md` with frontmatter:

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

## Adding a skill to nuvel itself (so all future agents get it)

If you want a skill bundled into every newly-scaffolded agent of a given backend, drop it under `nuvel/backends/<framework>/skills/<slug>/` in the nuvel repo and open a PR. See [Contributing](../contributing.md).
