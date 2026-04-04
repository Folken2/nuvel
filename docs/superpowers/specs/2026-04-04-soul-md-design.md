# SOUL.md Design Spec

> Give every generated agent a persistent identity layer via SOUL.md — personality, values, and boundaries that survive prompt rewrites.

## Core Principle

SOUL.md is a separate layer on top of the system prompt. The system prompt defines workflow, tools, and rules. SOUL.md defines who the agent is. It loads into the prompt like a context file but from a dedicated `soul/` directory. Designed for future self-updating (auto-dream, V2).

## SOUL.md Format

```markdown
---
name: agent-name
version: 1
---

# Identity
Who the agent is, its core purpose, one-line mission.

# Personality
Communication style, tone, how it relates to users.

# Values
What it prioritizes: accuracy, speed, friendliness, safety, etc.

# Boundaries
What it refuses to do, ethical limits, scope constraints.

# Evolution
How it should grow over time (placeholder for future auto-dream).
```

The frontmatter gives structure for future programmatic access. Sections are human-readable and LLM-friendly. The meta-agent fills these based on the agent's goal during creation.

## File Location

```
<agent_package>/
  soul/
    SOUL.md          # Agent identity
  prompt/
    instructions.py  # Workflow, tools, rules (loads SOUL.md)
  contexts/          # Domain knowledge (separate concern)
  ...
```

The `soul/` directory keeps the package root clean and leaves room for future soul-related files (evolution logs, personality snapshots, etc.).

## How It Loads

The generated agent's `prompt/instructions.py` reads `soul/SOUL.md` and prepends it to the system prompt:

```python
_SOUL_FILE = Path(__file__).parent.parent / "soul" / "SOUL.md"

def _load_soul() -> str:
    if _SOUL_FILE.is_file():
        content = _SOUL_FILE.read_text(encoding="utf-8").strip()
        if content:
            return content
    return ""

async def get_agent_instruction(ctx) -> str:
    soul = _load_soul()
    # ... build rest of prompt ...
    soul_block = f"\n{soul}\n\n" if soul else ""
    return f"""{soul_block}You are ...rest of prompt..."""
```

Soul content appears at the very top of the prompt — identity first, then capabilities and rules.

## Changes Required

### New template files
- `meta_agent/templates/{{agent_package}}/soul/SOUL.md.tmpl` — Template with `{{agent_name}}`, `{{agent_description}}` placeholders and default section content

### Modified template files
- `meta_agent/templates/{{agent_package}}/prompt/instructions.py.tmpl` — Add `_load_soul()` function and prepend soul to prompt

### Modified meta-agent files
- `meta_agent/prompt/instructions.py` — Add SOUL.md to the Generate phase: "Write a SOUL.md defining the agent's identity, personality, values, and boundaries"
- `meta_agent/tools/validate_tool.py` — Add SOUL.md check (warning if missing, not error — some agents may not need it)

### No changes to
- scaffold.py (already handles .tmpl files and directory creation)
- agent.py (soul loading happens in the prompt layer)
- plugins, config, tools

## Template SOUL.md Content

The default template (`SOUL.md.tmpl`) provides sensible defaults:

```markdown
---
name: {{agent_name}}
version: 1
---

# Identity
You are {{agent_name}} — {{agent_description}}.

# Personality
- Professional yet approachable
- Concise and direct
- Proactive — suggest next steps when appropriate

# Values
- Accuracy over speed — verify before asserting
- Transparency — explain reasoning when it adds value
- User autonomy — present options, let the user decide

# Boundaries
- Never fabricate data or make up facts
- Never expose internal implementation details to end users
- Acknowledge uncertainty honestly
- Stay within your domain expertise

# Evolution
This section will be updated as the agent learns from interactions.
```

The meta-agent overwrites this with a custom SOUL.md tailored to the agent's specific goal, domain, and personality requirements.

## Meta-Agent Prompt Addition

Add to the Generate phase (section 4), after the file list:

```
h. `<package>/soul/SOUL.md` — Agent identity: personality, values, boundaries. 
   Write a SOUL.md that defines who this agent is, not what it does (that's the system prompt's job).
   Identity sections: who it is, how it communicates, what it prioritizes, what it refuses to do.
```

## Validation

The validate tool adds a warning (not error) if `soul/SOUL.md` is missing:

```python
soul_file = os.path.join(package_dir, "soul", "SOUL.md")
if not os.path.isfile(soul_file):
    warnings.append("Missing soul/SOUL.md — agent has no identity layer")
```

## Success Criteria

1. Every scaffolded agent has `soul/SOUL.md` with sensible defaults
2. The generated agent's prompt loads SOUL.md and prepends it to the system prompt
3. The meta-agent writes a custom SOUL.md tailored to the agent's goal
4. validate_agent warns (not errors) if SOUL.md is missing
5. SOUL.md format has frontmatter (name, version) for future programmatic access
6. The soul/ directory exists and is ready for future evolution files
