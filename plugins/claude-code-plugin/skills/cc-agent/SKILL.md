---
name: cc-agent
description: Run autonomous coding tasks with Claude Code CLI — features, bug fixes, and PRs with Nuvel OrgMemory context.
---

# Claude Code CLI Agent Workflows

## Trigger Conditions
- Implementing a feature from a spec or issue
- Fixing a bug with OrgMemory-aware root cause analysis
- Refactoring code with team knowledge context
- Generating pull requests from task descriptions

## Prerequisites
- Claude Code CLI installed and configured (`cc-setup` skill)
- Nuvel OrgMemory MCP server connected
- Git repository with clean working tree
- CLAUDE.md configured with project context

## Steps

### 1. Prepare Task Context

Gather OrgMemory context:
```bash
claude -p "Search nuvel-orgmemory for all decisions and patterns related to <feature-area>. Summarize the key findings."
```

Create a focused task description:
```bash
cat > .claude/task.md << 'EOF'
# Task: <Title>

## Goal
<One-sentence description of what to achieve>

## Context from OrgMemory
<Paste relevant findings from the OrgMemory search above>

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Files to Modify
- src/path/to/file.ts (primary)
- src/path/to/test.ts (tests)
EOF
```

### 2. Run Claude Code in Agent Mode

**Feature implementation:**
```bash
claude -p "Read .claude/task.md. \
  Check nuvel-orgmemory for any additional context about these components. \
  Implement the feature according to the acceptance criteria. \
  Write comprehensive tests. \
  Ensure all existing tests still pass. \
  Run `npm test` after your changes."
```

**Bug fix with root cause analysis:**
```bash
claude -p "Investigate and fix the bug described in .claude/task.md. \
  Steps: \
  1. Reproduce the bug by running the failing test or steps \
  2. Use nuvel-orgmemory to check if similar bugs were reported before \
  3. Identify the root cause \
  4. Implement the fix with a regression test \
  5. Verify the fix resolves the issue \
  6. Run the full test suite"
```

**Refactor with guardrails:**
```bash
claude -p "Refactor <component> as described in .claude/task.md. \
  IMPORTANT: Before making any changes, check nuvel-orgmemory for \
  the component's architecture decisions and design patterns. \
  Follow these rules: \
  - Run tests after each logical change \
  - Do not change public API signatures unless specified \
  - Update JSDoc/docstrings for any modified functions"
```

### 3. Review Changes

```bash
# Review the diff
git diff --stat
git diff

# Check test results
npm test

# Lint check
npm run lint
```

### 4. Iterate if Needed

If issues found:
```bash
claude -p "The following tests failed after your changes: <paste failures>. \
  Fix the implementation while preserving the intended behavior."
```

### 5. Commit and Create PR

```bash
git add -A
git commit -m "feat(<scope>): <description>

Context from Nuvel OrgMemory: <key decisions referenced>
Closes #<issue>"

git push origin HEAD
gh pr create \
  --title "feat(<scope>): <description>" \
  --body "## Summary
<brief description>

## OrgMemory Context
- <key decision or pattern referenced>

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual verification steps completed"
```

### 6. Update OrgMemory

```bash
claude -p "Update nuvel-orgmemory with the following new knowledge from this task: \
  1. New design decisions made during implementation \
  2. Patterns established or modified \
  3. Any gotchas or learnings for future reference \
  Link to PR: <url>"
```

## Advanced Patterns

### Multi-Session Complex Features

For features requiring multiple Claude Code sessions:
```bash
# Session 1: Research and design
claude -p "Research the best approach for <feature> using nuvel-orgmemory \
  and codebase analysis. Write a design doc to .claude/design.md"

# Session 2: Core implementation
claude -p "Implement the design in .claude/design.md, starting with \
  the data model and core logic"

# Session 3: Integration and polish
claude -p "Complete the integration work from the previous session. \
  Add error handling, logging, and finalize tests."
```

### Parallel Workstreams

For independent changes that can run simultaneously:
```bash
# Start multiple Claude Code sessions in separate terminals
# Terminal 1:
claude -p "Implement the API endpoint for <feature-a>"

# Terminal 2:
claude -p "Implement the UI component for <feature-b>"
```

## Pitfalls
- **Context window limits**: Large codebases may exceed context. Use CLAUDE.md `/compact` or break work into focused sessions.
- **Permission denials**: Claude Code may refuse operations not in settings.json `permissions.allow`. Update settings if legitimate operations are blocked.
- **MCP disconnections**: If OrgMemory becomes unavailable mid-session, Claude Code may lose context. Restart the session if this happens.
- **Stale CLAUDE.md**: Outdated project instructions lead to misaligned implementations. Review CLAUDE.md monthly.

## Verification
1. All tests pass: `npm test`
2. Linter clean: `npm run lint`
3. Changes match acceptance criteria from task.md
4. OrgMemory updated with new knowledge
5. PR description references OrgMemory context