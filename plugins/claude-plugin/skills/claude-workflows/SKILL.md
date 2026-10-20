---
name: claude-workflows
description: Prompt patterns and workflows for using Claude (web/app) with Nuvel OrgMemory context — code, review, design, and documentation.
---

# Claude Workflows for Nuvel

## Trigger Conditions
- Code generation with OrgMemory-grounded patterns
- Architecture and design discussions
- Code review against team standards
- Documentation aligned with OrgMemory entries
- Research and analysis tasks with domain context

## Prerequisites
- Claude (web/app) configured for Nuvel (`claude-setup` skill)
- Nuvel OrgMemory open in a side tab for context gathering
- Project context template ready for session starts

## Workflow Templates

### Workflow 1: OrgMemory-Grounded Implementation

**When to use:** Building features that must follow established patterns

**Context Gathering (before prompt):**
1. Open Nuvel OrgMemory
2. Copy the relevant ADRs and coding standards
3. Note any existing component that's similar

**Prompt Template:**
```
## Implementation Request

### Task
Implement [feature description] in our [project name] codebase.

### OrgMemory Context
**Architecture Decision (ADR-042):**
[paste the relevant ADR from OrgMemory]

**Coding Standard (CS-Python-03):**
[paste the relevant standard from OrgMemory]

**Reference Implementation:**
[describe or paste existing similar code]

### Requirements
- Must follow the [pattern name] pattern from ADR-042
- Error handling must match project conventions per CS-Python-03
- Include unit tests following our test patterns
- Return types must use our standard Result<T, E> pattern

### Deliverables
1. Implementation code
2. Unit tests
3. Notes on what to record in OrgMemory after implementation
```

### Workflow 2: Architecture Design Partnership

**When to use:** Designing new systems, major refactors, or evaluating approaches

**Prompt Template:**
```
## Architecture Discussion

### Context from OrgMemory
**Current System Architecture:**
[paste from OrgMemory - component map]

**Existing Constraints (ADRs):**
[paste relevant ADRs]
- ADR-012: We chose [X] over [Y] because [reason]
- ADR-023: All services must communicate via [pattern]

### Proposal
[Describe what you want to build or change]

### Design Questions
1. Does this align with our existing architecture decisions?
2. What new patterns would this introduce?
3. What are the migration risks?
4. How would you recommend structuring this given our constraints?

### Deliverables
- Recommendation (proceed / revise / alternatives)
- Architecture diagram description
- List of new ADRs to create in OrgMemory
- Migration path if replacing existing systems
```

### Workflow 3: Standards-Based Code Review

**When to use:** Reviewing code against documented team standards

**Prompt Template:**
```
## Code Review Request

### Code to Review
```[language]
[paste the code]
```

### OrgMemory Standards to Check Against
**Coding Standard:**
[paste from OrgMemory]

**Architecture Decision:**
[paste relevant ADR]

**Testing Convention:**
[paste from OrgMemory]

### Review Focus Areas
- [ ] Compliance with coding standards above
- [ ] Alignment with architecture decisions
- [ ] Edge case handling
- [ ] Test coverage and quality

### Deliverables
- Critical issues (must fix)
- Standards violations (with reference to specific rule)
- Suggestions for improvement
- Overall assessment: approve / changes requested
```

### Workflow 4: Documentation with OrgMemory Links

**When to use:** Creating documentation that cross-references OrgMemory

**Prompt Template:**
```
## Documentation Request

### What to Document
[API / Component / Process / Architecture]

### OrgMemory References
**Related ADRs:**
- ADR-001: [title + summary]
- ADR-015: [title + summary]

**Related Components:**
- [Component A]: [brief description + OrgMemory link]
- [Component B]: [brief description + OrgMemory link]

**Related Standards:**
- [Standard 1]
- [Standard 2]

### Documentation Requirements
- Markdown format
- Include "Related Decisions" section with ADR references
- Include code examples from actual usage
- Note where OrgMemory should be consulted for deeper context

### Deliverables
Complete markdown document ready for our docs site
```

### Workflow 5: Incident Post-Mortem & Analysis

**When to use:** Analyzing production incidents with OrgMemory historical context

**Prompt Template:**
```
## Incident Analysis

### What Happened
[Timeline of events, error messages, impact]

### OrgMemory Context
**Past Similar Incidents:**
- INC-2024-03: [summary]
- INC-2024-07: [summary]

**Affected Component Architecture:**
[paste component docs from OrgMemory]

**Recent Changes:**
[list recent PRs or deploys to affected area]

### Analysis Request
1. Given the past incidents, what patterns do you see?
2. What is the likely root cause?
3. What immediate fixes are needed?
4. What long-term preventative measures should we take?
5. What should be recorded in OrgMemory as a new incident record?

### Deliverables
- Root cause analysis
- Fix recommendation (immediate + long-term)
- Incident record template for OrgMemory
```

## Quick Start Templates

### Feature Implementation (Short)
```
Implement [feature]. Follow pattern from ADR-[N]: [brief]. 
Tech: [stack]. Tests required: yes. 
Output: code + OrgMemory update notes.
```

### Bug Fix (Short)
```
Fix [bug]. Past incidents: [brief]. 
Component context from OrgMemory: [brief]. 
Add regression test. Output: fix + root cause.
```

### Design Review (Short)
```
Review design: [brief description]. 
Check against ADRs: [list]. 
Output: alignment check + risks + recommended ADR updates.
```

## OrgMemory Integration Checklist

Before ending a Claude session:
- [ ] Noted any new patterns that should become ADRs
- [ ] Identified documentation gaps to fill in OrgMemory
- [ ] Captured any learnings for future reference
- [ ] Updated project knowledge in Claude if OrgMemory changed significantly

## Pitfalls
- **Hallucinated standards**: Claude may cite plausible-sounding standards that don't exist in your OrgMemory. Always verify against actual OrgMemory content.
- **Context drift**: In long sessions, Claude may forget earlier OrgMemory context. Re-paste key context for complex multi-step tasks.
- **Over-reliance on memory**: Claude's project knowledge is static. Update it when OrgMemory changes, especially after major architectural decisions.
- **No code execution**: Web Claude can't run code or access files. For tasks requiring execution (running tests, checking git), use Claude Code CLI instead.

## Verification
1. Claude responses reference provided OrgMemory entries accurately
2. Code suggestions align with documented patterns
3. New patterns are flagged for OrgMemory recording
4. Output is production-quality and follows team standards