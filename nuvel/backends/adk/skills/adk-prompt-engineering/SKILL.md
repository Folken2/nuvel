---
name: adk-prompt-engineering
description: >
  Writing effective ADK system prompts — dynamic instructions with
  InstructionProvider, state placeholders, context injection, tone/style
  sections. Load this skill when creating or improving an agent's system prompt.
---

# ADK Prompt Engineering

**Version 1.0** | 2026-04-04

Write production-quality system prompts for Google ADK agents. This skill
covers prompt structure, dynamic instructions, state injection, and templates
for common agent types.

## System Prompt Structure

Every ADK agent prompt should follow this structure, in order:

```
1. IDENTITY        — Who the agent is, its name, role, and expertise
2. CAPABILITIES    — What the agent can do (and what it cannot)
3. WORKFLOW        — Step-by-step process for handling requests
4. TOOLS           — Documentation for each tool: when to use, parameters, examples
5. RULES           — Behavioral constraints and guidelines
6. GUARDRAILS      — Safety limits, forbidden actions, escalation triggers
```

### Section 1: Identity

Lead with a clear, specific identity statement. This anchors the LLM's
behavior more than any other section.

```
You are {agent_name}, a {role} specialized in {domain}.

You work for {company/context} and help users with {primary_task}.
```

**Do:**
- Be specific: "a senior data analyst" not "an assistant"
- Include domain: "specialized in SQL analytics and visualization"
- Set expertise level: "You have deep expertise in..."

**Don't:**
- Use generic identity: "You are a helpful AI assistant"
- Omit the domain — the LLM will default to general-purpose behavior

### Section 2: Capabilities

Explicitly list what the agent can and cannot do. This prevents hallucinated
capabilities and sets user expectations.

```
## Capabilities

You CAN:
- Query databases using SQL via the execute_sql tool
- Create charts and visualizations
- Export results to CSV

You CANNOT:
- Modify or delete data in the database
- Access external APIs or the internet
- Execute arbitrary code
```

### Section 3: Workflow

Provide a numbered step-by-step process. This is the most impactful section
for consistent agent behavior.

```
## Workflow

When a user makes a request, follow these steps:

1. **Understand**: Clarify the request if ambiguous. Identify the data needed.
2. **Plan**: Determine which tables and queries are needed. Share the plan.
3. **Execute**: Run SQL queries using execute_sql. Check results for errors.
4. **Analyze**: Interpret the results. Look for patterns and outliers.
5. **Present**: Format results as a clear summary with charts if appropriate.
6. **Follow up**: Ask if the user needs refinements or additional analysis.
```

**Key principle:** The workflow should describe the agent's _thinking process_,
not just list actions. Include decision points ("if ambiguous, clarify first").

### Section 4: Tools

Document each tool the agent has access to. The LLM needs to know _when_ to
use each tool, not just that it exists.

```
## Tools

### execute_sql
- **When to use**: For any data retrieval or analysis that requires querying the database
- **Parameters**: `query` (string) — a valid PostgreSQL query
- **Notes**: Read-only access. Queries timeout after 30s. Always use LIMIT for exploration.
- **Example**: `execute_sql(query="SELECT count(*) FROM orders WHERE status = 'pending'")`

### create_chart
- **When to use**: When the user requests a visualization or when data would benefit from a chart
- **Parameters**: `chart_type` (bar|line|pie), `data` (JSON), `title` (string)
- **Notes**: Prefer bar charts for comparisons, line for trends, pie for proportions (<6 categories).
```

**Key principle:** Explain the _decision criteria_ for using each tool, not
just the API. "When to use" is more important than parameter documentation.

### Section 5: Rules

Behavioral guidelines that shape the agent's style and approach.

```
## Rules

- Always show your SQL queries before executing them
- Use markdown tables for structured data (under 20 rows)
- Round numbers to 2 decimal places unless precision matters
- When results are unexpected, explain possible reasons
- Respond in the same language the user uses
```

### Section 6: Guardrails

Hard limits that the agent must never violate. Put these last — they serve as
a "final check" anchor.

```
## Guardrails

- NEVER execute DELETE, UPDATE, INSERT, or DROP statements
- NEVER expose raw database credentials or connection strings
- NEVER share data from one user's session with another
- If a query would return more than 10,000 rows, ask for filters first
- If uncertain about data sensitivity, ask the user before displaying
```

## Dynamic vs Static Prompts

### Static Prompts (Default)

Use a plain string for the `instruction` parameter. Best for agents with
fixed behavior.

```python
agent = LlmAgent(
    name="analyst",
    model="gemini-2.5-flash",
    instruction="You are a data analyst. ...",
)
```

### State Placeholders

ADK automatically replaces `{key}` placeholders in the instruction string
with values from `session.state`. Use these for injecting dynamic context
without a full InstructionProvider.

```python
agent = LlmAgent(
    name="analyst",
    model="gemini-2.5-flash",
    instruction="""You are a data analyst for {company_name}.

Current user: {user_name} (role: {user_role})
Current mode: {mode}

Available datasets: {available_datasets}
""",
)
```

For details on state placeholders and prefixes, load:
`load_skill_resource("adk-prompt-engineering", "references/state-placeholders.md")`

### InstructionProvider (Dynamic)

Use an async function that receives `ReadonlyContext` and returns the full
prompt string. Best for prompts that need to:
- Load external context files
- Build tool documentation dynamically
- Change behavior based on complex state logic

```python
async def get_instruction(ctx) -> str:
    mode = ctx.state.get("mode", "default")
    # ... build prompt dynamically
    return full_prompt_string

agent = LlmAgent(
    name="analyst",
    model="gemini-2.5-flash",
    instruction=get_instruction,
)
```

For the complete InstructionProvider pattern with context file loading, load:
`load_skill_resource("adk-prompt-engineering", "references/instruction-provider-pattern.md")`

## Context Injection Pattern

For agents that need substantial reference material, use a `contexts/`
directory pattern:

```
agent_package/
  contexts/
    domain_knowledge.md
    style_guide.md
    tool_reference.md
  agent.py
```

Load contexts in the InstructionProvider:

```python
from pathlib import Path

CONTEXTS_DIR = Path(__file__).parent / "contexts"

async def get_instruction(ctx) -> str:
    domain = (CONTEXTS_DIR / "domain_knowledge.md").read_text()
    style = (CONTEXTS_DIR / "style_guide.md").read_text()

    return f"""You are an analyst.

## Domain Knowledge
{domain}

## Style Guide
{style}

## Current Session
User: {ctx.state.get('user_name', 'Unknown')}
"""
```

**Benefits:**
- Context files are version-controlled and reviewable
- Non-engineers can edit domain knowledge without touching Python
- Prompts stay readable — large reference blocks live in separate files

## The three prompt tiers (cache stability)

Every agent scaffolded by nuvel assembles its system prompt as an
`InstructionProvider` that builds three tiers, concatenated in this fixed
order (`prompt/instructions.py.tmpl`):

1. **Stable** — `build_stable_tier()`: static identity/persona (the persona
   scaffold, SOUL.md, and the system frame). Byte-identical across turns
   within a session — nothing in this tier is allowed to vary turn to turn.
2. **Session** — `build_session_tier(ctx)`: session-specific but
   slow-changing — the structured user-profile block plus memory retrieved
   for the current conversation (relevance-conditioned, not the whole
   memory file). Changes only when the underlying profile or retrieved
   memory actually changes.
3. **Volatile** — `build_volatile_tier(ctx)`: per-turn reminders (today's
   date, an infra warning, the last tool error, a near-budget notice).
   Rides the tail of the prompt and is expected to differ every turn.

`get_agent_instruction(ctx)` returns `"\n\n".join([stable, session, volatile])`
(skipping any empty tier) — stable always leads, volatile always trails.

**Why the ordering is a cost decision, not a formatting one.** Providers
cache on prompt *prefix*: a cache hit costs a fraction of a fresh input
token, but the cache only stays warm while the leading bytes of the prompt
stay identical. Put anything per-turn near the front — a timestamp, a
turn counter, an error message — and you invalidate the cached prefix on
every single turn, silently multiplying input-token cost with no change in
behavior. Ordering stable-then-session-then-volatile is what keeps the
expensive, rarely-changing identity/persona text cache-hot turn after turn
while only the cheap tail pays for freshness. This matters most for exactly
the agents nuvel targets: long stable personas plus heavy retrieved memory,
run over many turns.

**The authoring contract.** When adding instruction content to a generated
agent, decide which tier it belongs to and don't mix them:

- New persona text, tone rules, or identity content → the **stable** tier.
- Anything derived from session state (user profile fields, retrieved
  memory) → the **session** tier.
- Anything that changes every turn (dates, warnings, error nudges, budget
  notices) → the **volatile** tier.
- **Never interpolate a per-turn value into the stable tier** — that's the
  one mistake that defeats the whole scheme, since it makes the "stable"
  prefix move on every turn anyway.

**Graceful degradation.** `build_session_tier` wraps each source
independently in `try`/`except`: if the user-profile store or the memory
retrieval call fails, that section is simply omitted rather than raising —
the session tier degrades to whatever sources succeeded (down to empty)
instead of breaking prompt assembly for the whole turn
(`test_session_tier_degrades_when_sources_fail`).

This contract is pinned by the 12 tests in `tests/test_prompt_tiers.py` —
in particular `test_full_prompt_stable_prefix_survives_volatile_change` and
`test_stable_tier_ignores_volatile_state` assert that changing only volatile
state leaves the stable prefix byte-identical. A future edit that breaks
tier isolation fails that suite.

Event compaction (see `adk-long-horizon-sessions`) is a structurally
separate mechanism — it rewrites the session's *event* history, not the
system-instruction tiers described here, and `instructions.py.tmpl` never
reads events or does any compaction-related work. The two are independent
levers on the same turn's total cost: compaction buys headroom on the event
side of the ledger, a byte-identical stable prefix buys a cheap prompt on
the instruction side. Tune them as two separate cost decisions, not one
feeding the other.

## Tool Documentation Best Practices

When an agent has many tools, dedicate a section of the prompt to explaining
decision criteria:

```
## When to Use Each Tool

Use this decision tree:

Need data from the database? → execute_sql
Need to create a chart? → create_chart
Need to export results? → export_csv
Need to send a notification? → send_alert
Unsure? → Ask the user what they need

### Tool Interaction Patterns

1. Always query data BEFORE creating charts (you need the data first)
2. After any SQL error, explain the error and suggest a fix
3. Only send alerts when the user explicitly requests them
```

## Prompt Templates

For complete, production-ready prompt templates for common agent types, load:
`load_skill_resource("adk-prompt-engineering", "references/prompt-templates.md")`

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Generic identity ("helpful assistant") | Be specific: role, domain, expertise level |
| No workflow section | Add numbered steps — this drives consistent behavior |
| Tools listed without "when to use" | Add decision criteria for every tool |
| Guardrails buried in the middle | Put guardrails last for anchoring effect |
| Prompt over 4000 tokens | Move reference material to context files |
| No capability boundaries | Explicitly list what the agent CANNOT do |
| State placeholders without defaults | Use `ctx.state.get("key", "default")` in InstructionProvider |
| Mixing concerns in one section | Keep identity, workflow, tools, and rules in separate sections |
