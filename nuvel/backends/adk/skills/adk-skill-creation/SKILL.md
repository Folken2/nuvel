---
name: adk-skill-creation
description: >-
  Creating valid SKILL.md files following the agentskills.io specification —
  frontmatter, instructions, references directory, progressive disclosure
  (L1/L2/L3), and SkillToolset wiring in agent.py. Load this skill when
  generating domain skills for an agent.
---

# ADK Skill Creation — The Meta-Skill

This skill teaches you how to create SKILL.md files for ADK agents. Every skill you generate must follow this specification exactly.

## Directory Structure

```
skills/
  my-skill/
    SKILL.md              # Required — frontmatter + instructions
    references/           # Optional — L3 detailed resource files
      patterns.md
      examples.md
      api-reference.md
```

## SKILL.md File Format

Every SKILL.md has two parts: YAML frontmatter and a markdown body.

```markdown
---
name: my-skill-name
description: >-
  What this skill does and WHEN to load it. Under 1024 characters.
  Be specific: "SEO checklist for blog posts" not "A helpful skill".
---

# Skill Title

## Step-by-step instructions here (L2 content)

1. First step...
2. Second step...

## References

- Load `pattern-name` for detailed implementation patterns.
```

## Frontmatter Rules

1. **`name`** (REQUIRED): kebab-case, max 64 characters. Examples: `security-review`, `api-integration`, `data-pipeline`.
2. **`description`** (REQUIRED): under 1024 characters. This is the **L1 metadata** — the only thing the LLM sees before deciding whether to load the full skill. Write it to answer: "When should I load this skill?"

## Progressive Disclosure — L1 / L2 / L3

This is the most important concept in skill design.

| Level | What | Size | When loaded |
|-------|------|------|-------------|
| **L1** | `name` + `description` from frontmatter | ~100 tokens | Every turn — the LLM always sees this via `list_skills` |
| **L2** | Full markdown body of SKILL.md | ~500-2000 tokens | On demand — when the LLM calls `load_skill("my-skill")` |
| **L3** | Files in `references/` directory | Variable | On demand — when the LLM calls `load_skill_resource("my-skill", "patterns.md")` |

### Why this matters

- L1 is cheap (loaded every turn), so keep descriptions concise but specific.
- L2 is the working instruction set — step-by-step, actionable, under 500 lines.
- L3 holds the bulk: full code examples, specs, reference tables. Only loaded when needed.

## Writing Good Descriptions (L1)

The description determines whether the LLM loads the skill. Be specific about **when** and **what for**.

**Good descriptions:**
- "OWASP Top 10 security review checklist for Python web applications. Load when reviewing code for security vulnerabilities."
- "Building REST API wrapper tools with retry logic, rate limiting, and error handling. Load when creating tools that call external HTTP APIs."

**Bad descriptions:**
- "A helpful skill for developers." (too vague — when would the LLM load this?)
- "This skill helps with things." (meaningless)

## Writing Good Instructions (L2)

3. Use **numbered steps** — the LLM follows them sequentially.
4. Be **imperative**: "Check for X", "Add Y", "Validate Z" — not "You might want to consider..."
5. **Reference L3 resources by name**: "Load `api-patterns` for complete request/response examples."
6. Keep under **500 lines**. If instructions grow beyond that, move details to references.
7. Include a **quick-start template** or minimal example directly in the instructions.
8. End with a **References section** listing all L3 resources and what each contains.

## Writing Good References (L3)

9. One file per topic: `patterns.md`, `examples.md`, `api-reference.md`.
10. Include **complete, runnable code** — not pseudocode.
11. Use clear headings so the LLM can scan quickly.
12. Keep each reference file focused — under 300 lines if possible.

## The References Table (ADK 2.0 convention)

When a skill has 2 or more reference files, **list them in a markdown table near
the top of the SKILL.md body**, with three columns:

| Column | Content |
|--------|---------|
| **Resource** | The file slug (the argument you'd pass to `load_skill_resource`). Use `kebab-case` matching the filename without `.md`. |
| **Description** | What the file teaches — concrete enough that the LLM can pick the right one without first loading it. Avoid filler ("details on X"); say what's inside ("How `@node` resolves params from `ctx.state`; async/generator nodes"). |
| **Load when** | The trigger condition: a task type, a question shape, or a symptom the LLM would notice. |

### Why a table beats a bullet list

The pre-2.0 convention was `- Load `name` for details on X.` at the bottom of
SKILL.md. Two problems with that:

1. **Ambiguity.** "details on X" tells the LLM nothing about whether `X.md`
   actually contains what's needed — so it loads, reads, then often loads
   another. Wasted turns.
2. **Position.** Bullets at the bottom mean the LLM has to read the whole
   SKILL.md before seeing the routing. The table near the top lets it route
   immediately.

The new convention: a **routing table** the LLM can scan in one pass to pick
the right L3 file on the first try, without `load_skill_resource` round-trips.

### Example table

```markdown
| Resource | Description | Load when |
|----------|-------------|-----------|
| api-patterns | Retry/backoff with jitter, rate-limit handling, idempotency keys | Building a tool that calls an external HTTP API |
| error-handling | Mapping HTTP status codes to user-facing error messages; redaction rules | The agent needs to report a failure to the user |
| auth-flows | OAuth 2.0 code flow, PKCE, refresh-token rotation | The API requires user-delegated auth |
```

Place it after a short Overview/Steps section but **before** the long-form
prose. Reference files mentioned inline in the body are still fine — the table
is the index, the inline mentions are the cross-refs.

## Skill Design Patterns

Before writing a skill from scratch, load `adk-skill-design-patterns` to identify which canonical pattern fits. The 5 patterns (Tool Wrapper, Generator, Reviewer, Inversion, Pipeline) provide proven structures with skeleton templates. Use the decision matrix to match the agent's needs to the right pattern, then load the pattern's reference for a starting template.

## Naming Conventions

- Skill directory: `kebab-case` (e.g., `security-review/`)
- SKILL.md: always exactly `SKILL.md` (uppercase)
- References: `kebab-case.md` (e.g., `owasp-checklist.md`)

## Complete Example — Minimal Skill

```markdown
---
name: code-review-checklist
description: >-
  Structured code review checklist covering correctness, security, performance,
  and maintainability. Load this skill when reviewing pull requests or code
  changes.
---

# Code Review Checklist

## Steps

1. **Correctness**: Does the code do what it claims? Check edge cases.
2. **Security**: Look for injection, auth bypass, data exposure. Load `security-checks` for the full OWASP checklist.
3. **Performance**: Check for N+1 queries, unnecessary allocations, missing indexes.
4. **Maintainability**: Are names clear? Is complexity manageable? Are there tests?
5. **Style**: Does it follow the project's conventions?

## Output Format

Produce a structured review with sections for each category above. Use severity levels: CRITICAL, WARNING, INFO.

## References

- Load `security-checks` for OWASP Top 10 patterns to look for.
- Load `performance-patterns` for common performance anti-patterns.
```

## Skills proposed by the agent itself

A generated agent can optionally propose its own `SKILL.md` files: the
skill curator (`NUVEL_SKILL_CURATOR`, default off) observes tool usage
during a run and writes candidate skills into `NUVEL_SKILL_PROPOSALS_DIR`
for a human to review before they become live skills — the conventions in
this skill are exactly what such a proposal should be judged against
before it's accepted. Load `adk-memory-self-improvement` for how the
curator decides when to propose and where proposals land.

## Wiring Skills into an Agent

After creating SKILL.md files, they must be wired into the agent via `SkillToolset`. Load the `skilltoolset-wiring` reference for the complete wiring code.

The short version:

```python
from google.adk.tools.skill_toolset import SkillToolset
from google.adk.skills import load_skill_from_dir

skill = load_skill_from_dir(Path("skills/my-skill"))
toolset = SkillToolset(skills=[skill])
# Pass toolset to agent's tools list
```

This auto-generates three tools the LLM can call:
- `list_skills` — returns L1 metadata for all skills (name + description)
- `load_skill(skill_name)` — returns L2 content (full instructions)
- `load_skill_resource(skill_name, resource_filename)` — returns L3 content

## References

- Load `skill-spec` for the complete agentskills.io specification summary.
- Load `example-skills` for 4 complete example SKILL.md files across different domains.
- Load `skilltoolset-wiring` for the full agent.py wiring code with SkillToolset.
