---
name: codex-review
description: Use OpenAI Codex CLI to perform automated code review on pull requests with Nuvel OrgMemory context.
---

# Codex CLI PR Review

## Trigger Conditions
- A pull request is opened and needs review
- Pre-merge quality gate checks
- Need context-aware review using team knowledge from OrgMemory
- Want automated first-pass review before human review

## Prerequisites
- Codex CLI installed and authenticated (`codex-setup` skill)
- Nuvel OrgMemory MCP server configured
- GitHub CLI (`gh`) installed and authenticated
- The PR number to review

## Steps

### 1. Fetch PR Context

```bash
# Get the PR number (either from user or from current branch)
PR_NUMBER="<number>"

# Fetch PR details
gh pr view $PR_NUMBER --json number,title,body,baseRefName,headRefName

# Checkout the PR branch locally
gh pr checkout $PR_NUMBER
```

### 2. Gather OrgMemory Context

```bash
codex exec "Search nuvel-orgmemory for architecture decisions, \
  coding standards, and patterns relevant to the files changed in this PR. \
  List all relevant findings."
```

### 3. Run Automated Review

```bash
codex exec --approval-policy never \
  "Review PR #${PR_NUMBER}. \
   \
   CONTEXT FROM ORGMEMORY: \
   First check nuvel-orgmemory for any architecture decisions or \
   coding standards that apply to these changes. \
   \
   REVIEW CHECKLIST: \
   1. Security: Check for injection risks, exposed secrets, unsafe deserialization \
   2. Performance: Identify N+1 queries, unnecessary allocations, blocking operations \
   3. Correctness: Verify edge cases, null handling, error propagation \
   4. Architecture: Ensure changes follow established patterns from OrgMemory \
   5. Testing: Check test coverage for new code paths \
   6. Style: Verify consistency with project conventions \
   \
   OUTPUT: \
   - Summary of changes \
   - Critical issues (must-fix) \
   - Warnings (should-fix) \
   - Suggestions (nice-to-have) \
   - OrgMemory alignment check \
   \
   Post each finding as a review comment."
```

### 4. Post Review to GitHub

```bash
# If Codex outputs review comments, post them
codex exec "Post the review findings as a GitHub PR review on #${PR_NUMBER}. \
  Use 'gh pr review' with --approve, --comment, or --request-changes \
  based on the severity of findings."
```

Or post manually based on Codex output:
```bash
# Approve if no issues
gh pr review $PR_NUMBER --approve --body "Automated review by Codex CLI with Nuvel OrgMemory context. No issues found."

# Request changes if issues found
gh pr review $PR_NUMBER --request-changes --body "$(cat review-findings.md)"
```

### 5. Follow Up

```bash
# If changes were requested, update OrgMemory with review patterns
codex exec "Update nuvel-orgmemory with any new review patterns \
  or anti-patterns discovered during this review."
```

## Review Focus Areas (Per Language)

### TypeScript/JavaScript
- Check for missing error boundaries in React components
- Verify proper async/await error handling
- Ensure no `any` types without justification
- Check bundle size impact

### Python
- Verify type hints on public APIs
- Check for proper context manager usage
- Ensure async functions are properly awaited
- Validate Pydantic model usage

### Go
- Check error handling (no ignored errors)
- Verify proper defer usage
- Ensure context propagation
- Check goroutine lifecycle management

## Pitfalls
- **False positives**: Codex may flag patterns it doesn't fully understand. Always verify critical issues manually.
- **Stale OrgMemory**: If team conventions changed but OrgMemory wasn't updated, the review may apply outdated standards. Periodically audit OrgMemory freshness.
- **Large PRs**: For PRs with 500+ line diffs, break the review into focused chunks by file or component.
- **API rate limits**: Large reviews with many MCP calls may hit rate limits. Use `--approval-policy never` to avoid interactive delays.

## Verification
1. Review comments are posted on the PR: `gh pr view $PR_NUMBER --comments`
2. OrgMemory was consulted for context (verify in Codex logs)
3. All critical issues have corresponding GitHub review comments
4. Review aligns with documented team standards