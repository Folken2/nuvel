---
name: cursor-review
description: Use Cursor IDE with Nuvel OrgMemory to review changes — diff analysis, standards checking, and pre-commit review.
---

# Cursor IDE Code Review

## Trigger Conditions
- Pre-commit review of your own changes
- Reviewing a teammate's PR in Cursor
- Checking changes against OrgMemory coding standards
- Quick diff analysis before pushing

## Prerequisites
- Cursor IDE configured for Nuvel (`cursor-setup` skill)
- Nuvel OrgMemory MCP server connected
- `.cursorrules` with Nuvel directives
- Changes to review (staged, unstaged, or PR branch)

## Steps

### 1. Review Your Own Changes (Pre-Commit)

**Stage the changes you want to review:**
```bash
git add <files>
```

**In Cursor Chat (Cmd+L):**
```
Review my staged changes. 

First, search nuvel-orgmemory for our coding standards and any 
architecture decisions relevant to the files I've changed.

Then review the staged diff against:
1. Our coding standards from OrgMemory
2. Architecture decisions from OrgMemory
3. General best practices (error handling, edge cases, security)

Output:
- Critical issues (must fix before commit)
- Standards violations (with specific OrgMemory reference)
- Suggestions for improvement
```

### 2. Review Staged Diff with OrgMemory

For a more structured review:

```
## Code Review Request

### Context
I've made changes to [describe what you changed].

### Review Instructions
1. Query nuvel-orgmemory for:
   - Coding standards for [language]
   - Architecture decisions about [component]
   - Any past bugs or incidents in these files

2. Review the staged changes (git diff --cached) against:
   - SECURITY: injection risks, exposed secrets, unsafe operations
   - CORRECTNESS: null handling, edge cases, error propagation
   - STANDARDS: alignment with OrgMemory conventions
   - PERFORMANCE: N+1 queries, unnecessary work, blocking calls
   - TESTING: new code paths covered by tests

3. For each issue found, cite the specific OrgMemory standard or ADR.

### Output Format
## Review Summary
[Brief assessment]

## Issues Found
### Critical
- [ ] file:line — [description] (violates: [OrgMemory standard])

### Warnings
- [ ] file:line — [description]

### Suggestions
- file:line — [description]

## OrgMemory Alignment
- Followed: [list standards followed]
- Deviated: [list any deviations with justification]
```

### 3. Review a Teammate's PR Locally

```bash
# Checkout the PR branch
gh pr checkout <PR_NUMBER>

# Or fetch manually
git fetch origin pull/<PR_NUMBER>/head:pr-<PR_NUMBER>
git checkout pr-<PR_NUMBER>
```

**In Cursor Chat:**
```
Review PR #<PR_NUMBER> which I've checked out locally.

## OrgMemory Context
Search nuvel-orgmemory for:
1. Architecture decisions about the components changed in this PR
2. Any coding standards that apply
3. Past incidents or bugs in these files

## Review the Diff
Compare this branch against main:
```
git diff main...HEAD
```

## Review Focus
- Security vulnerabilities
- Architecture alignment with documented decisions
- Code quality and maintainability
- Test coverage adequacy
- Documentation completeness

### PR Details
Title: [PR title]
Description: [PR description]
Files changed: [list from gh pr view]

### Output
Structured review with:
- Summary
- Critical / Warning / Suggestion items
- Specific OrgMemory references for each standards-related finding
- Overall recommendation (approve / changes requested / comment)
```

### 4. Use Inline Chat for Focused Review

For reviewing specific files or sections:

1. Select code in the editor
2. Press Cmd+K (macOS) / Ctrl+K (Windows/Linux)
3. Prompt:
```
Review this code against our OrgMemory standards.
Check: error handling, edge cases, pattern consistency.
```

### 5. Post-Review Actions

**If issues found, fix them:**
```
Fix the issues identified in the review:
1. [Issue 1 from review]
2. [Issue 2 from review]
Preserve the intended behavior while addressing these issues.
```

**After fixes:**
```bash
# If it's your own changes:
git add -A
git commit --amend  # or create a new commit

# If reviewing a teammate's PR, post your review:
gh pr review <PR_NUMBER> --comment --body "$(cat review-notes.md)"
# Or approve:
gh pr review <PR_NUMBER> --approve
# Or request changes:
gh pr review <PR_NUMBER> --request-changes --body "$(cat review-notes.md)"
```

**Update OrgMemory:**
```
Based on this review, update nuvel-orgmemory with:
- Any new patterns or anti-patterns discovered
- Updated component documentation if architecture changed
- Link to the PR for future reference
```

## Review Checklist (Quick Reference)

| Category | What to Check | OrgMemory Source |
|----------|--------------|-----------------|
| Security | Injection, secrets, auth bypass | Security Standards |
| Architecture | Pattern compliance, coupling | ADRs for affected components |
| Error Handling | Proper propagation, user messages | Error Handling Standards |
| Types | No `any`, proper null handling | TypeScript/Python Standards |
| Testing | New code paths covered | Testing Standards |
| Performance | N+1, blocking calls, allocations | Performance Guidelines |
| Naming | Consistent, clear, domain-aligned | Naming Conventions |

## Pitfalls
- **Reviewing unstaged changes**: Cursor's AI can see the current file content but may not have full diff context. Use `git diff` output for comprehensive review.
- **Large diffs**: For PRs with 300+ lines changed, review in chunks by file or component. The AI's context window is limited.
- **OrgMemory mismatch**: If OrgMemory standards are outdated vs. current practices, flag the discrepancy rather than blindly enforcing old rules.
- **Over-reliance on AI review**: AI review catches patterns and standards violations but can miss subtle logic bugs. Critical code paths need human review.

## Verification
1. All critical issues are resolved before commit/merge
2. Review notes reference specific OrgMemory entries
3. Fixes applied are verified (tests pass, behavior correct)
4. OrgMemory updated with new review insights if applicable
5. PR review is posted (if reviewing teammate's work)