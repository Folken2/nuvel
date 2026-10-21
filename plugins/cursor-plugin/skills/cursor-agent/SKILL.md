---
name: cursor-agent
description: Run agent-mode tasks in Cursor IDE with Nuvel OrgMemory context — autonomous feature work, refactors, and debugging.
---

# Cursor IDE Agent Workflows

## Trigger Conditions
- Implementing features in Cursor with agent mode (Cmd+I)
- Running agent-mode refactors with OrgMemory guardrails
- Debugging with OrgMemory incident context
- Multi-file changes where agent mode is more efficient than manual editing

## Prerequisites
- Cursor IDE configured for Nuvel (`cursor-setup` skill)
- Nuvel OrgMemory MCP server connected
- `.cursorrules` with Nuvel directives in project
- Git repository with clean working tree

## Steps

### 1. Prepare Agent Context

Before starting an agent task, gather OrgMemory context:

**In Cursor Chat (Cmd+L):**
```
Search nuvel-orgmemory for all architecture decisions and patterns related 
to [component/feature area]. List the key decisions, constraints, and 
reference implementations I need to be aware of.
```

Save findings for the agent task.

### 2. Run Agent Mode with OrgMemory Context

**Open Agent Mode:** Cmd+I (macOS) / Ctrl+I (Windows/Linux)

**Feature Implementation Prompt:**
```
## Task
Implement [feature description] in this codebase.

## Context from OrgMemory
[Paste the OrgMemory search results from step 1]

## Approach
1. First, open and read the files you'll need to modify
2. Check OrgMemory standards before writing code
3. Implement the feature following our documented patterns
4. Write or update tests for all new code paths
5. Run `[test command]` to verify nothing breaks
6. Flag any new decisions that should be recorded in OrgMemory

## Files to Modify
- src/[path]/[file].ts (primary implementation)
- src/[path]/[file].test.ts (tests)

## Acceptance Criteria
- [ ] [Criterion 1]
- [ ] [Criterion 2]
- [ ] All existing tests pass
- [ ] New code follows OrgMemory standards
```

**Bug Fix Prompt:**
```
## Bug
[Description of the bug]

## OrgMemory Context
- Past similar incidents: [paste from OrgMemory if any]
- Component architecture: [paste from OrgMemory]

## Steps
1. Reproduce the bug (use the failing test or describe steps)
2. Use OrgMemory to check for past related issues
3. Identify root cause
4. Fix with a regression test
5. Run full test suite
6. Document the fix pattern if it's worth adding to OrgMemory
```

### 3. Review Agent Changes

After the agent completes:

```bash
# Review the diff in Cursor's source control panel
# Or use terminal:
git diff --stat
git diff

# Run tests
npm test  # or your project's test command

# Check with OrgMemory standards
# In Cursor Chat:
# "Review these changes against our coding standards from OrgMemory. 
#  Flag any deviations."
```

### 4. Iterate if Needed

In Agent Mode (Cmd+I):
```
The [specific test/behavior] is not working correctly after your changes. 
The expected behavior is: [describe]. 
Fix this while preserving the rest of the implementation.
```

### 5. Commit and Document

```bash
git add -A
git commit -m "feat(<scope>): <description>

OrgMemory: Follows ADR-[N] pattern for [pattern name]
Refs #[issue]"

# In Cursor Chat, update OrgMemory:
# "Update nuvel-orgmemory with the following from this implementation:
#  - New pattern established: [describe]
#  - Any gotchas for future reference"
```

### 6. Create PR

Use the GitHub Pull Requests extension in Cursor, or terminal:
```bash
gh pr create \
  --title "feat(<scope>): <description>" \
  --body "## Changes
<summary>

## OrgMemory Alignment
- Follows ADR-[N]: [pattern name]
- [Any new decisions or deviations with justification]

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual verification steps"
```

## Advanced: Composer Mode for Multi-File Refactors

For large refactors, use Cursor's Composer (Cmd+Shift+I):

```
## Refactor Task
Refactor [component/module] to use [new pattern].

## OrgMemory Context
- Current pattern documented in ADR-[N]
- Target pattern documented in ADR-[M]

## Rules
1. Do not change public API signatures unless specified
2. Run tests after each file change — if tests break, fix before continuing
3. Update imports across the codebase
4. After the refactor, list all files changed for OrgMemory documentation update

## Scope
Files under: src/[module]/
Tests under: src/[module]/__tests__/
```

## Agent Mode Best Practices

### DO
- Provide OrgMemory context explicitly in each agent prompt
- Use specific file paths rather than broad "fix everything" prompts
- Review agent diffs carefully — the agent can make unexpected changes
- Run tests after each agent session
- Commit working states before starting new agent tasks

### DON'T
- Give the agent a prompt that spans 10+ independently testable changes
- Skip providing OrgMemory context — the agent will default to generic patterns
- Trust the agent's test results without verifying
- Run agent mode on uncommitted changes
- Use agent mode for simple one-line fixes (use inline editing instead)

## Pitfalls
- **Agent ignores OrgMemory**: If the agent isn't using OrgMemory context, MCP server may be disconnected. Check Cursor MCP settings.
- **Scope creep**: Agents can make changes beyond your intended scope. Review the full diff, not just the files you specified.
- **Test flakiness**: The agent may write tests that pass locally but are flaky. Review test quality, not just pass/fail.
- **MCP latency**: OrgMemory queries add latency to agent operations. For simple tasks, skip OrgMemory context to stay fast.

## Verification
1. All tests pass after agent completes
2. Diff is scoped to intended changes (check with `git diff --stat`)
3. OrgMemory standards are followed (verify with quick chat review)
4. New patterns are documented back to OrgMemory
5. Commit message references OrgMemory context used