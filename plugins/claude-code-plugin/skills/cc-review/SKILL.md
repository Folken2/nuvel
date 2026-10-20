---
name: cc-review
description: Use Claude Code CLI to review pull requests with Nuvel OrgMemory context for team standards alignment.
---

# Claude Code CLI PR Review

## Trigger Conditions
- Pull request is ready for review
- Need OrgMemory-informed code review
- Pre-merge quality gate automation
- Pair-review with a human reviewer for critical changes

## Prerequisites
- Claude Code CLI installed and configured (`cc-setup` skill)
- Nuvel OrgMemory MCP server connected
- GitHub CLI (`gh`) installed and authenticated
- CLAUDE.md with project conventions

## Steps

### 1. Fetch and Checkout PR

```bash
PR_NUMBER="<number>"

# View PR details
gh pr view $PR_NUMBER --json number,title,body,baseRefName,headRefName,additions,deletions,files

# Checkout locally
gh pr checkout $PR_NUMBER
```

### 2. Gather OrgMemory Context

```bash
claude -p "For PR #${PR_NUMBER}, search nuvel-orgmemory for: \
  1. Architecture decisions about the components modified in this PR \
  2. Coding standards relevant to the languages/frameworks used \
  3. Any past incidents or bugs related to these files \
  4. Team conventions for the patterns used \
  Output a structured context summary."
```

### 3. Run Comprehensive Review

```bash
claude -p "Review PR #${PR_NUMBER} using the following framework:

## OrgMemory Context (from previous search):
<paste OrgMemory context summary here>

## Review Framework:
### Security
- Injection vulnerabilities (SQL, XSS, command)
- Exposed secrets or API keys
- Authentication/authorization bypasses
- Unsafe deserialization or eval

### Performance
- N+1 queries and unnecessary database calls
- Missing indexes or inefficient queries
- Memory leaks or unbounded growth
- Blocking operations in async contexts

### Correctness
- Edge cases: null/undefined, empty collections, boundary values
- Error handling: proper propagation, user-friendly messages
- Race conditions and concurrency issues
- Type safety violations

### Architecture (cross-reference with OrgMemory)
- Does this follow existing patterns?
- Are new abstractions consistent with the codebase?
- Is there unnecessary coupling?
- Are there violations of established decisions?

### Testing
- Coverage of new code paths (happy path, error path, edge cases)
- Test quality (meaningful assertions, not just coverage metrics)
- Integration test coverage for API changes
- Are mocking practices appropriate?

### Maintainability
- Clear naming and documentation
- Complexity hotspots (cyclomatic complexity, deep nesting)
- TODO/FIXME comments that should be resolved
- Breaking changes properly flagged

## Output Format:
Generate a review in this structure:

### Summary
Brief overview of the changes and overall assessment.

### Critical Issues (must fix before merge)
- [ ] Issue 1: <description> (file:line)
- [ ] Issue 2: <description> (file:line)

### Warnings (should fix)
- [ ] Warning 1: <description> (file:line)

### Suggestions (nice to have)
- Suggestion 1: <description>

### OrgMemory Alignment
- ✅ Aligned: <decision/pattern followed>
- ⚠️ Deviation: <decision not followed, with justification if valid>"
```

### 4. Post Review

Based on findings:

```bash
# If no critical issues:
gh pr review $PR_NUMBER --approve \
  --body "## Automated Review by Claude Code + Nuvel OrgMemory

$(cat review-output.md)

Reviewed against OrgMemory standards and project conventions."

# If issues found:
gh pr review $PR_NUMBER --request-changes \
  --body "$(cat review-output.md)"

# For comments only:
gh pr review $PR_NUMBER --comment \
  --body "$(cat review-output.md)"
```

### 5. Post-Review Actions

```bash
# If new patterns or anti-patterns were found:
claude -p "Update nuvel-orgmemory with review insights from PR #${PR_NUMBER}: \
  - Any new patterns that should be documented \
  - Anti-patterns to avoid in future \
  - Updated component documentation if architecture changed"
```

## Review Severity Guidelines

| Severity | Criteria | Action |
|----------|----------|--------|
| Critical | Security vulnerability, data loss risk, breaks existing functionality | Request changes, block merge |
| Warning | Performance regression, missing error handling, test gap | Request changes or comment |
| Suggestion | Code style, naming, minor optimizations | Comment, non-blocking |

## Pitfalls
- **Review scope creep**: Focus review on the PR's stated purpose. Avoid suggesting unrelated refactors.
- **OrgMemory staleness**: If team practices evolved but OrgMemory is outdated, note the discrepancy rather than blocking on it.
- **Large PRs**: For PRs over 400 lines, request the author to split or focus review on the most critical files.
- **False confidence**: Claude Code may miss subtle bugs. Critical PRs should always have human review in addition to automated review.

## Verification
1. Review posted on the PR: `gh pr view $PR_NUMBER --comments`
2. All critical issues have corresponding comments
3. Review references specific OrgMemory decisions/patterns
4. Post-review OrgMemory update completed (if applicable)