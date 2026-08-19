---
name: getting-started
description: How this agent's skills work and how to add one. Read when asked what you can do, when someone wants to extend you with a new capability, or as a worked example of the SKILL.md format.
---

# Skills, and how to add one

A skill is a folder with a `SKILL.md` in it. This file is one.

## What the agent sees

At startup the agent loads only the **frontmatter** of every skill — the `name`
and `description` above. That list is what `list_skills` returns. The body you
are reading now is loaded only when something calls `read_skill("getting-started")`.

That split is the whole design. Fifty skills cost a few hundred tokens of
prompt until one is actually relevant, then the full instructions arrive
exactly when they're needed.

## Adding one

```
{{agent_package}}/skills/
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

Set `BUZZ_SKILLS_DIR` to load skills from somewhere else entirely — a mounted
volume, a shared repo — without touching the package.

## Writing the body

The body is instructions for a capable colleague who hasn't done *this*
particular task before. Concrete steps, real examples, the failure modes worth
naming. Skip the background theory; the model has that already.
