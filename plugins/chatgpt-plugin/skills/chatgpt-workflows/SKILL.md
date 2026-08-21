---
name: chatgpt-workflows
description: Prompt templates and workflows for using ChatGPT with Nuvel — code generation, review, debugging, and architecture design.
---

# ChatGPT Workflows for Nuvel

## Trigger Conditions
- Need code generation with OrgMemory context
- Architecture design discussion before implementation
- Debugging with past incident context from OrgMemory
- Documentation generation linking to OrgMemory entries
- Quick prototyping before handing off to CLI agents

## Prerequisites
- ChatGPT configured for Nuvel (`chatgpt-setup` skill)
- Access to Nuvel OrgMemory for context gathering
- Project context file (`.chatgpt/project-context.md`) available

## Workflow Templates

### Workflow 1: Context-Aware Code Generation

**When to use:** Implementing a new feature or component

**Step 1: Gather OrgMemory Context**

Before starting, open Nuvel and copy relevant entries:
- Architecture decisions for the affected domain
- Coding standards for the language/framework
- Existing component patterns to follow

**Step 2: Use the Code Generation Prompt**

```
## Code Generation Request

### Task
Implement [specific feature/component] for [project name].

### OrgMemory Context
**Architecture Decision:** [paste from OrgMemory]
**Coding Standards:** [paste from OrgMemory]
**Reference Implementation:** [paste example of similar existing code]

### Requirements
1. [Requirement 1]
2. [Requirement 2]
3. Must follow the [pattern name] pattern as documented in OrgMemory

### Output Format
Please provide:
1. A brief explanation of the approach and how it aligns with OrgMemory decisions
2. The implementation code with proper error handling
3. Unit tests following our testing conventions
4. Any new OrgMemory entries that should be created (architecture decisions, component docs)

### Tech Stack
- Language: [language]
- Framework: [framework]
- Key libraries: [libraries]
```

**Step 3: Review and Adapt**

Review the generated code against OrgMemory standards. Note any deviations.

**Step 4: Persist to OrgMemory**

After implementing, add any new decisions or patterns to OrgMemory.

---

### Workflow 2: Debugging with Past Incident Context

**When to use:** Investigating a bug, especially if similar issues occurred before

**Step 1: Search OrgMemory for Similar Incidents**

Open Nuvel and search for:
- Past bugs in the same component
- Related error messages
- Recent changes to the affected files

**Step 2: Use the Debugging Prompt**

```
## Debugging Request

### Issue Description
[What's happening, error messages, reproduction steps]

### OrgMemory Context
**Past Similar Incidents:** [paste from OrgMemory if any]
**Recent Changes:** [list recent commits or PRs to the affected component]
**Component Architecture:** [paste component docs from OrgMemory]

### Environment
- Branch: [branch name]
- Dependencies: [relevant versions]
- Steps to reproduce: [exact steps]

### Debugging Approach
Please help me:
1. Analyze the error based on the provided context and past incidents
2. Suggest potential root causes (ranked by likelihood)
3. Propose fixes for the most likely cause
4. Recommend tests to prevent regression

### Constraints
- Must maintain backward compatibility with [existing API/interface]
- Test coverage must not decrease
```

**Step 3: Verify and Document**

After fixing, document the incident in OrgMemory:
- Root cause
- Fix applied
- Prevention measures

---

### Workflow 3: Architecture Design Review

**When to use:** Designing new systems or major refactors

**Step 1: Gather Current Architecture Context**

From OrgMemory, collect:
- Existing architecture decisions in the affected domain
- System boundary diagrams
- Current data flow patterns

**Step 2: Use the Architecture Review Prompt**

```
## Architecture Design Review

### Proposal
[Describe the proposed architecture change or new system]

### Current Architecture (from OrgMemory)
**Existing Decisions:**
[paste relevant architecture decisions]

**Current System Boundaries:**
[paste component relationships]

**Data Flow:**
[describe or paste current data flow]

### Design Constraints
1. [Constraint 1, e.g., "Must handle 10x current load"]
2. [Constraint 2, e.g., "No downtime during migration"]
3. [Constraint 3, e.g., "Backward compatible with existing API"]

### Review Request
Please evaluate the proposal against:
1. **Alignment**: Does it follow existing architecture patterns from OrgMemory?
2. **Trade-offs**: What are the pros/cons vs. alternatives?
3. **Risks**: What could go wrong? Migration complexity?
4. **Migration Path**: How to transition from current to proposed architecture?
5. **OrgMemory Updates**: What new decisions need to be recorded?

### Output Format
- Summary of alignment with existing OrgMemory decisions
- Recommendation (proceed / revise / reject) with justification
- If revise: specific changes needed
- Migration plan outline
- List of OrgMemory entries to create/update
```

**Step 3: Record Decision in OrgMemory**

After the review, create an Architecture Decision Record (ADR) in OrgMemory.

---

### Workflow 4: Documentation Generation

**When to use:** Creating or updating documentation linked to OrgMemory

**Prompt:**
```
## Documentation Request

### What to Document
[Component, API, system, or process to document]

### OrgMemory Context
**Existing Docs:** [paste related documentation from OrgMemory]
**Architecture Decision:** [paste relevant ADR]
**Component Spec:** [paste component specification]

### Documentation Type
- [ ] API Reference
- [ ] Architecture Overview
- [ ] Getting Started Guide
- [ ] Troubleshooting Guide
- [ ] Decision Record

### Requirements
- Link to relevant OrgMemory entries
- Include code examples from the actual codebase
- Follow our documentation template from OrgMemory

### Output
Generate documentation in markdown format with:
1. Overview linking to OrgMemory context
2. Detailed sections with code examples
3. Cross-references to related OrgMemory entries
4. A section for "Related Decisions" with OrgMemory links
```

---

## Quick Reference: Prompt Templates

### Bug Fix
```
Fix [bug description]. 
Context from OrgMemory: [paste]. 
Affected files: [paths]. 
Test with: [test command].
```

### Feature Implementation
```
Implement [feature]. 
Must follow [pattern] from OrgMemory: [paste]. 
Tech stack: [stack]. 
Tests required: [yes/no].
```

### Code Review
```
Review this code for [project]. 
Check against standards from OrgMemory: [paste]. 
Focus on: [security/performance/correctness].
```

### Refactor
```
Refactor [component] to [new pattern]. 
Current patterns from OrgMemory: [paste]. 
Do not change: [public API/behavior].
```

## Pitfalls
- **Context window limits**: ChatGPT has a finite context window. For very large OrgMemory entries, summarize the key points.
- **No real-time OrgMemory access**: ChatGPT can't query OrgMemory. Always provide context upfront. For automated access, use Codex CLI or Claude Code CLI.
- **Hallucinated OrgMemory references**: ChatGPT may invent plausible-sounding but non-existent OrgMemory entries. Always verify references against actual OrgMemory content.
- **Stale responses**: ChatGPT's knowledge has a cutoff date. Supplement with current context from OrgMemory for recent decisions.

## Verification
1. Generated code compiles and passes tests
2. Outputs reference actual OrgMemory entries (not hallucinated)
3. New patterns align with documented OrgMemory standards
4. Any new decisions are recorded back to OrgMemory