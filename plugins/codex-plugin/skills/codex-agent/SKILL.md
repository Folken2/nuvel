---
name: codex-agent
description: Run autonomous coding tasks with OpenAI Codex CLI — features, bug fixes, refactors, and PRs.
---

# Codex CLI Agent Workflows

## Trigger Conditions
- User requests: "build feature X", "fix bug Y", "refactor Z"
- Need to implement a feature from a spec or issue
- Generating pull requests from task descriptions
- Running Codex in agent mode for autonomous code changes

## Prerequisites
- Codex CLI installed and authenticated (`codex-setup` skill)
- Nuvel OrgMemory MCP server configured
- A git repository with clean working tree
- Clear task description or issue reference

## Steps

### 1. Prepare the Task Context

Gather relevant context before running the agent:
```bash
# Check OrgMemory for related decisions and architecture
codex exec "Search OrgMemory for any design decisions or docs related to <feature/component>"
```

Create a task spec file (or use an existing GitHub issue):
```bash
cat > .codex/task.md << 'EOF'
# Task: <Brief Title>

## Description
<Detailed description of what needs to be built or changed>

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Related
- OrgMemory: <link to relevant knowledge graph entry>
- Issue: #<number>
EOF
```

### 2. Run Codex in Agent Mode

Basic feature implementation:
```bash
codex exec --approval-policy on-request \
  "Read .codex/task.md and implement the described feature. \
   Check nuvel-orgmemory for any relevant architecture decisions first. \
   Write tests, implement the changes, and ensure all existing tests pass."
```

Bug fix workflow:
```bash
codex exec --approval-policy on-request \
  "Fix the bug described in .codex/task.md. \
   First reproduce the issue, then identify the root cause, \
   implement the fix with tests, and verify the fix resolves the issue."
```

### 3. Review and Iterate

After Codex completes:
```bash
# Review the diff
git diff

# Run the test suite
npm test  # or your project's test command

# If issues found, ask Codex to fix them
codex exec "The tests are failing with: <error>. Fix the implementation."
```

### 4. Commit and Create PR

```bash
git add -A
git commit -m "feat: <description>

Implemented via Codex CLI with Nuvel OrgMemory context.
Closes #<issue-number>"

# Push and create PR
git push origin HEAD
gh pr create --title "feat: <description>" --body "Automated implementation via Codex CLI."
```

### 5. Update OrgMemory

After merging, persist new knowledge:
```bash
codex exec "Update nuvel-orgmemory with the new design decisions \
  and patterns introduced in this implementation. \
  Reference the merged PR for context."
```

## Advanced: Multi-File Refactors

For large refactors spanning many files:
```bash
codex exec --approval-policy on-request \
  "Perform a refactor of <component> to <new pattern>. \
   Step 1: Analyze all files that need changes. \
   Step 2: Run the existing test suite to establish baseline. \
   Step 3: Apply changes file by file, running tests after each. \
   Step 4: Update any documentation."
```

## Pitfalls
- **Large changes**: For changes spanning 20+ files, break into multiple Codex sessions to avoid context overflow.
- **Approval policy**: Always use `on-request` (not `auto`) for production codebases. Auto-mode should only be used in sandbox environments.
- **MCP context**: If Codex is not using OrgMemory, verify the MCP server is running and reachable.
- **Git state**: Always ensure a clean or committed working tree before running agent mode. Codex may amend unexpected files.

## Verification
1. All tests pass: `npm test`
2. Linter clean: `npm run lint`
3. Diff is scoped to the intended changes: `git diff main...HEAD`
4. OrgMemory updated with new knowledge