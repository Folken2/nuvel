# agentskills.io Specification Summary

## Directory Structure

```
skills/
  <skill-name>/
    SKILL.md                # Required — skill definition
    references/             # Optional — L3 reference files
      *.md
    assets/                 # Optional — images, data files
    scripts/                # Optional — executable scripts
```

## SKILL.md Format

```yaml
---
# REQUIRED fields
name: kebab-case-name        # Max 64 characters, [a-z0-9-]
description: >-              # Max 1024 characters
  What the skill does and when to use it.

# OPTIONAL fields (not commonly used)
# version: 1.0.0
# tags: [python, security]
# author: your-name
---

Markdown body here — the L2 instructions.
```

## Frontmatter Schema

### Required Fields

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `name` | string | kebab-case, max 64 chars, `[a-z0-9-]` | Unique identifier for the skill |
| `description` | string | Max 1024 characters | L1 metadata — shown to LLM for skill selection |

### Naming Rules

- Must be kebab-case: `my-skill-name`
- Only lowercase letters, numbers, and hyphens
- Max 64 characters
- Must be unique within the skills directory
- Must match the directory name

### Description Rules

- Max 1024 characters (hard limit)
- Should answer: "When should the LLM load this skill?"
- Include the trigger condition: "Load this skill when..."
- Be specific about the domain and use case
- Avoid generic phrases ("helpful", "useful", "good")

## Instructions Body

The markdown body after the frontmatter is the L2 content.

### Guidelines

- **Max 500 lines** — move details to references if longer
- Use numbered steps for sequential instructions
- Use headings to organise sections
- Include at least one minimal example or template
- End with a References section listing L3 resources
- Write in imperative mood: "Check X", "Add Y", "Validate Z"

### Recommended Sections

```markdown
# Skill Title

## Overview (optional, 2-3 sentences)

## Steps
1. First step...
2. Second step...

## Quick-Start Template
(minimal working example)

## Common Pitfalls (optional)

## References
- Load `resource-name` for details on X.
```

## References Directory

- Files must be markdown (`.md`)
- Use kebab-case filenames: `api-patterns.md`, `error-handling.md`
- Each file should be focused on one topic
- Include complete, runnable code examples
- Recommended: under 300 lines per file

## Function Routing Table (ADK 2.0)

When a skill has 2 or more reference files, list them in a **3-column markdown
table** placed near the top of the SKILL.md body. The columns are fixed:

| Column | Content |
|--------|---------|
| `Resource` | The file slug — `kebab-case`, matching the filename without `.md`. This is the argument to `load_skill_resource(skill_name, resource)`. |
| `Description` | What the file teaches. Concrete enough to disambiguate from sibling references. Avoid "details on X" — say what's actually inside. |
| `Load when` | The trigger condition (task type, question shape, symptom). One short clause. |

### Why the Description column

Before ADK 2.0, references were listed as bullet links at the bottom of
SKILL.md: `- Load `api-patterns` for details on API patterns.` The LLM had to
load each candidate file just to see if it was the right one. The Description
column eliminates that round-trip — the LLM can choose correctly from the
SKILL.md alone.

### Canonical example

```markdown
| Resource | Description | Load when |
|----------|-------------|-----------|
| api-patterns | Retry/backoff with jitter, rate-limit handling, idempotency keys | Building a tool that calls an external HTTP API |
| error-handling | Mapping HTTP status codes to user-facing messages; redaction rules | The agent needs to report a failure to the user |
| auth-flows | OAuth 2.0 code flow, PKCE, refresh-token rotation | The API requires user-delegated auth |
```

### Rules

- One row per reference file. No grouping rows, no merged cells.
- Resource cells contain only the slug — no backticks, no `.md`, no path prefix.
- Description cells are sentence fragments (no trailing period required); be
  specific about *what is inside* the file, not what topic it relates to.
- `Load when` cells are also sentence fragments; phrase them as triggers the
  LLM would notice mid-conversation.
- Place the table after a one-paragraph Overview, **before** the main body /
  Steps section.

### When to skip the table

A skill with **0 or 1** reference files doesn't need a routing table — the L2
body can mention the single reference inline. The table earns its place when
the LLM has to choose between siblings.

## Progressive Disclosure Summary

```
L1: name + description     (~100 tokens)  → Always visible via list_skills
L2: SKILL.md body          (~500-2000 tokens) → Loaded via load_skill
L3: references/*.md        (variable)     → Loaded via load_skill_resource
```

The LLM autonomously decides when to load L2 and L3 based on the task at hand. Good L1 descriptions are critical for this to work.

## Size Budget Guidelines

| Component | Recommended Max | Hard Limit |
|-----------|----------------|------------|
| `name` | 30 chars | 64 chars |
| `description` | 500 chars | 1024 chars |
| SKILL.md body | 200 lines | 500 lines |
| Single reference file | 300 lines | None (but larger = slower to load) |
| Total references | 5-10 files | None |
