# Skill Design Patterns Integration — Design Spec

> **Date:** 2026-04-08
> **Status:** Approved
> **Goal:** Integrate the 5 canonical skill design patterns into the meta-agent so it can intelligently recommend and scaffold pattern-based skills for generated agents.

---

## Context

The meta-agent creates ADK agents with skills, but has no taxonomy for *what kind* of skill to create. The 5 canonical skill design patterns (Tool Wrapper, Generator, Reviewer, Inversion, Pipeline) give the meta-agent a structured vocabulary to recommend the right skill architecture based on the user's agent requirements.

Source: [5 Agent Skill Design Patterns Every ADK Developer Should Know](https://lavinigam.com/posts/adk-skill-design-patterns/)

---

## Design

### 1. New Skill: `adk-skill-design-patterns`

**Location:** `meta_agent/skills/adk-skill-design-patterns/`

**Structure:**
```
adk-skill-design-patterns/
  SKILL.md                          # L2: Taxonomy + decision matrix
  references/
    pattern-tool-wrapper.md         # L3: Deep guide + skeleton
    pattern-generator.md            # L3: Deep guide + skeleton
    pattern-reviewer.md             # L3: Deep guide + skeleton
    pattern-inversion.md            # L3: Deep guide + skeleton
    pattern-pipeline.md             # L3: Deep guide + skeleton
```

**SKILL.md (L2) contains:**
1. Overview of the 5 patterns (2-3 sentences each)
2. Decision matrix — maps agent characteristics to recommended patterns
3. Instructions: load this skill during Step 2, match agent to patterns, load L3 references for skeletons
4. Guidance that patterns combine (e.g., Inversion + Reviewer + Tool Wrapper)

**Each L3 reference contains:**
- When to use this pattern
- Architecture / flow diagram (ASCII)
- Complete SKILL.md skeleton template
- Reference file skeleton (where applicable)
- Real-world example

### 2. Meta-Agent Prompt Update

**File:** `meta_agent/prompt/instructions.py`

**Step 2 (Design) changes:**
- Load `adk-skill-design-patterns` to understand the 5 canonical patterns
- Match the user's agent to patterns using the decision matrix
- For each recommended pattern, load its L3 reference for skeleton templates
- Propose skills specifying which design pattern each follows

**Step 4 (Generate) changes:**
- Add `load_skill("adk-skill-design-patterns")` to the skill loading list
- Load specific pattern references for skeleton templates

### 3. Cross-Reference in `adk-skill-creation`

**File:** `meta_agent/skills/adk-skill-creation/SKILL.md`

Add a "Skill Design Patterns" section cross-referencing `adk-skill-design-patterns` so the meta-agent is reminded of patterns even when loading skill creation guidance directly.

---

## What Does NOT Change

- Template system (`templates/`) — no new auto-included files
- Scaffold logic (`scaffold.py`) — untouched
- Other 4 existing skills — untouched
- Agent wiring (`agent.py`) — SkillToolset auto-discovers the new skill

## Summary

| Component | Change | Files |
|-----------|--------|-------|
| New skill | `adk-skill-design-patterns/` + 5 L3 references | 6 new |
| Meta-agent prompt | Step 2 + Step 4 pattern awareness | 1 edit |
| Skill creation skill | Cross-reference | 1 edit |
| **Total** | | **6 new, 2 edits** |
