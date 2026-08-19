---
name: getting-started
description: How this Hermes profile is put together — SOUL.md, config.yaml, and the skills directory — and how to add a skill. Read when asked what this agent can do, when someone wants to extend it with a new capability, or as a worked example of the SKILL.md format.
---

# This profile, and how to extend it

A Hermes agent is three things in one directory:

```
{{agent_package}}/
├── SOUL.md        identity: who I am, what I'm for, how I speak
├── config.yaml    model, max_turns, platforms, enabled skills
└── skills/
    └── getting-started/
        └── SKILL.md      ← this file
```

`SOUL.md` is loaded every turn — it's short on purpose. `config.yaml` is
operational, not behavioural: changing `model.default` changes which model
answers, never *what* the agent is.

## What the agent sees

At startup only the **frontmatter** of each skill is loaded — the `name` and
`description` above. The body you're reading arrives only when this skill is
actually opened.

That split is the whole design. Fifty skills cost a few hundred prompt tokens
until one becomes relevant, then the full instructions show up exactly when
they're needed. It's also why the description matters more than it looks:
it's the only thing the agent has when deciding whether to read further.

## Adding one

```
skills/
├── getting-started/
│   └── SKILL.md
└── your-new-skill/
    └── SKILL.md          ← add this; nothing else to wire
```

The frontmatter is two keys:

```markdown
---
name: incident-triage
description: Triage a production incident — severity rubric, who to page, and what to capture in the timeline. Read when an alert fires or someone reports an outage.
---
```

Write the `description` for a reader deciding *whether to open the file*: name
the situation, not the topic. "Read when an alert fires" beats "about incidents."

`skills.enabled` in `config.yaml` is a force-load list, not a registry — leave
it empty and every skill in this directory is still discoverable. Add a slug
there only when the skill must be in context from the first token.

## Writing the body

The body is instructions for a capable colleague who hasn't done *this*
particular task before: concrete steps, real examples, the failure modes worth
naming. Skip the background theory — the model already has it.
