# SOUL.md Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SOUL.md identity layer to every generated agent — a markdown file defining personality, values, and boundaries that loads at the top of the system prompt.

**Architecture:** A `SOUL.md.tmpl` template in the `soul/` directory, loaded by the generated agent's `instructions.py.tmpl`. The meta-agent writes a custom SOUL.md during the Generate phase. Validation warns if missing.

**Tech Stack:** Python (pathlib for file loading), YAML frontmatter in markdown

---

## File Structure

### New template files:
- `meta_agent/templates/{{agent_package}}/soul/SOUL.md.tmpl` — Default identity template with placeholders

### Modified template files:
- `meta_agent/templates/{{agent_package}}/prompt/instructions.py.tmpl` — Add `_load_soul()` and prepend to prompt

### Modified meta-agent files:
- `meta_agent/prompt/instructions.py` — Add SOUL.md to Generate phase
- `meta_agent/tools/validate_tool.py` — Add SOUL.md warning check

### Test files:
- Modified: `tests/test_end_to_end.py` — Verify SOUL.md in scaffolded agents
- Modified: `tests/test_validate.py` — Verify SOUL.md warning

---

### Task 1: SOUL.md Template + Prompt Loading

**Files:**
- Create: `meta_agent/templates/{{agent_package}}/soul/SOUL.md.tmpl`
- Modify: `meta_agent/templates/{{agent_package}}/prompt/instructions.py.tmpl`
- Modify: `tests/test_end_to_end.py`

- [ ] **Step 1: Write test for SOUL.md in scaffolded agents**

Add to `tests/test_end_to_end.py`:

```python
def test_scaffold_includes_soul_md(self):
    """Scaffolded agents have soul/SOUL.md with correct content."""
    scaffold_agent("soul-test-agent", output_dir=self.tmpdir, description="A soulful agent")
    agent_dir = os.path.join(self.tmpdir, "soul-test-agent")

    soul_path = os.path.join(agent_dir, "soul_test_agent", "soul", "SOUL.md")
    assert os.path.isfile(soul_path), "soul/SOUL.md not found"

    content = open(soul_path, encoding="utf-8").read()
    assert "soul-test-agent" in content
    assert "# Identity" in content
    assert "# Personality" in content
    assert "# Values" in content
    assert "# Boundaries" in content
    assert "# Evolution" in content
    assert "{{" not in content  # No unresolved placeholders

def test_scaffold_prompt_loads_soul(self):
    """Scaffolded agent's instructions.py has _load_soul function."""
    scaffold_agent("prompt-soul-agent", output_dir=self.tmpdir)
    agent_dir = os.path.join(self.tmpdir, "prompt-soul-agent")

    instructions = open(
        os.path.join(agent_dir, "prompt_soul_agent", "prompt", "instructions.py"),
        encoding="utf-8",
    ).read()
    assert "_load_soul" in instructions
    assert "soul" in instructions.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_end_to_end.py -v
```

Expected: FAIL — `soul/SOUL.md not found`

- [ ] **Step 3: Create SOUL.md.tmpl**

Create `meta_agent/templates/{{agent_package}}/soul/SOUL.md.tmpl`:

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

- [ ] **Step 4: Update instructions.py.tmpl to load SOUL.md**

Replace the full content of `meta_agent/templates/{{agent_package}}/prompt/instructions.py.tmpl` with:

```python
"""
Agent instruction builder for {{agent_name}}.
"""

import logging
from pathlib import Path

from ..utils.date_utils import get_current_date_info, format_current_date

logger = logging.getLogger(__name__)

_SOUL_FILE = Path(__file__).parent.parent / "soul" / "SOUL.md"
_CONTEXTS_DIR = Path(__file__).parent.parent / "contexts"


def _load_soul() -> str:
    """Load the agent's SOUL.md identity layer."""
    if _SOUL_FILE.is_file():
        try:
            content = _SOUL_FILE.read_text(encoding="utf-8").strip()
            if content:
                logger.info("Loaded SOUL.md (%d chars)", len(content))
                return content
        except Exception as e:
            logger.warning("Failed to read SOUL.md: %s", e)
    return ""


def _load_context() -> str:
    """Load context files from the contexts/ directory."""
    if not _CONTEXTS_DIR.is_dir():
        return ""
    parts = []
    for ctx_file in sorted(_CONTEXTS_DIR.glob("*.md")):
        try:
            content = ctx_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        except Exception as e:
            logger.warning("Failed to read context %s: %s", ctx_file.name, e)
    return "\n\n".join(parts)


async def get_agent_instruction(ctx) -> str:
    """Generate agent instruction. ADK InstructionProvider callback."""
    formatted_date = format_current_date()
    soul = _load_soul()
    context = _load_context()

    soul_block = f"{soul}\n\n" if soul else ""
    context_block = ""
    if context:
        context_block = f"\n\n# Domain Knowledge\n\n{context}"

    return f"""{soul_block}{{agent_system_prompt}}

Today's date: {formatted_date}
{context_block}"""
```

- [ ] **Step 5: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_end_to_end.py -v
```

Expected: All PASS (including new tests)

- [ ] **Step 6: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All 86 existing + 2 new PASS

- [ ] **Step 7: Commit**

```bash
git add meta_agent/templates/ tests/test_end_to_end.py
git commit -m "feat: add SOUL.md template and prompt loading

Every generated agent gets soul/SOUL.md with identity, personality,
values, boundaries, and evolution sections. Loaded at the top of
the system prompt by instructions.py."
```

---

### Task 2: Validation Warning + Meta-Agent Prompt Update

**Files:**
- Modify: `meta_agent/tools/validate_tool.py`
- Modify: `meta_agent/prompt/instructions.py`
- Modify: `tests/test_validate.py`

- [ ] **Step 1: Write test for SOUL.md validation warning**

Add to `tests/test_validate.py`:

```python
def test_missing_soul_md_warns(self):
    """Missing soul/SOUL.md produces a warning, not an error."""
    # Remove the SOUL.md that scaffold created
    soul_path = os.path.join(self.agent_dir, "test_agent", "soul", "SOUL.md")
    if os.path.isfile(soul_path):
        os.remove(soul_path)

    result = _validate_agent_impl(self.agent_dir)
    assert result["status"] == "ok"  # Not an error
    assert any("SOUL.md" in w for w in result["warnings"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/test_validate.py::TestValidateAgentImpl::test_missing_soul_md_warns -v
```

Expected: FAIL — no SOUL.md warning produced

- [ ] **Step 3: Add SOUL.md check to validate_tool.py**

In `meta_agent/tools/validate_tool.py`, add after the skills directory check (after line 104, before the `status = "ok"` line):

```python
    # 7. SOUL.md should exist (warning, not error)
    soul_file = os.path.join(package_dir, "soul", "SOUL.md")
    if not os.path.isfile(soul_file):
        warnings.append("Missing soul/SOUL.md — agent has no identity layer")
```

- [ ] **Step 4: Run test**

```bash
source .venv/bin/activate && python -m pytest tests/test_validate.py -v
```

Expected: All PASS

- [ ] **Step 5: Update meta-agent system prompt**

In `meta_agent/prompt/instructions.py`, add to the file list in the Generate phase (section 4). After line `g. .env.example`:

Add:

```
h. `<package>/soul/SOUL.md` — Agent identity: personality, values, boundaries.
   Write a SOUL.md that defines who this agent is, not what it does (that's the system prompt's job).
   Sections: Identity (mission), Personality (tone/style), Values (priorities), Boundaries (limits), Evolution (future growth).
```

- [ ] **Step 6: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All PASS

- [ ] **Step 7: Commit and push**

```bash
git add meta_agent/tools/validate_tool.py meta_agent/prompt/instructions.py tests/test_validate.py
git commit -m "feat: add SOUL.md validation warning + meta-agent prompt update

validate_agent warns if soul/SOUL.md is missing. Meta-agent prompt
now instructs writing SOUL.md during the Generate phase."
git push
```

---

## Implementation Notes

### Task Dependencies
- Task 1 must complete first (template + tests need to exist before validation)
- Task 2 depends on Task 1 (validation checks for the file Task 1 creates)

### Testing Strategy
- Task 1: E2E tests verify scaffolded agents have SOUL.md with correct content and prompt loading
- Task 2: Validate test checks the warning is produced when SOUL.md is missing
- Existing tests continue to pass (scaffolded agents now include soul/ so validation improves)
