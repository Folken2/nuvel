---
name: cursor-setup
description: Configure Cursor IDE with Nuvel rules, MCP server integration, and project settings for OrgMemory-aware development.
---

# Cursor IDE Setup for Nuvel

## Trigger Conditions
- Setting up Cursor IDE for a Nuvel-integrated project
- Configuring MCP servers in Cursor for OrgMemory access
- Establishing project-wide .cursorrules for team consistency
- Onboarding new team members to Cursor + Nuvel workflow

## Prerequisites
- Cursor IDE installed (https://cursor.com)
- A Nuvel account with OrgMemory access (https://nuvel.dev)
- Git repository for the project
- Nuvel API key for MCP server access

## Steps

### 1. Configure MCP Server in Cursor

Cursor supports MCP servers natively. Add the Nuvel OrgMemory server:

**Cursor Settings > MCP > Add New MCP Server**

Or create `~/.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "nuvel-orgmemory": {
      "command": "npx",
      "args": ["-y", "@nuvel/mcp-server-orgmemory"],
      "env": {
        "NUVEL_API_KEY": "${NUVEL_API_KEY}",
        "NUVEL_ORG_ID": "${NUVEL_ORG_ID}"
      }
    }
  }
}
```

Set environment variables in your shell profile:
```bash
export NUVEL_API_KEY="nv-..."
export NUVEL_ORG_ID="org_..."
```

Restart Cursor after adding the MCP server.

### 2. Create Project .cursorrules

Create `.cursorrules` in your project root:

```
# Nuvel Integration
You are connected to Nuvel OrgMemory via the nuvel-orgmemory MCP server.
This provides access to our team's knowledge graph: architecture decisions, 
coding standards, component docs, and past incidents.

## Core Directives
1. **Before implementing**: Check nuvel-orgmemory for existing architecture 
   decisions and patterns relevant to the task.
2. **During implementation**: Follow coding standards from OrgMemory. 
   If the standard is unclear, query OrgMemory for clarification.
3. **After implementation**: Update OrgMemory with new decisions, patterns, 
   or learnings. Flag if a new ADR is needed.

## Code Style
- [Language]-specific: [key conventions from your team]
- Error handling: Always handle errors explicitly. No silent failures.
- Type safety: Use strict types. No `any` without justification.
- Testing: Every new function/module requires tests.

## Project Architecture
[Brief description of your project's architecture — keep this in sync with OrgMemory]

## MCP Tools Available
- nuvel-orgmemory: Query team knowledge, search ADRs, retrieve coding standards

## Response Pattern
When I ask you to implement something:
1. First, silently query OrgMemory for relevant context
2. Implement following documented patterns
3. After completion, note what should be updated in OrgMemory
```

### 3. Configure .cursorignore

Create `.cursorignore` to exclude files from Cursor's indexing:

```
# Dependencies
node_modules/
venv/
.venv/
__pycache__/
*.pyc

# Build artifacts
dist/
build/
.next/
out/

# Secrets and environment
.env
.env.local
*.pem
*.key
credentials/

# Large data files
*.zip
*.tar.gz
*.csv
*.parquet
data/

# IDE files (other than Cursor)
.vscode/
.idea/
*.swp
*.swo

# Generated code
*.generated.*
*-generated.*
```

### 4. Set Up Cursor Rules for Nuvel Workflows

Create `.cursor/rules/nuvel-code-review.md`:
```markdown
# Nuvel Code Review Rules

When reviewing code:
1. Check changes against OrgMemory coding standards
2. Verify alignment with architecture decisions
3. Flag any pattern deviations
4. Check for missing error handling
5. Verify test coverage for new code paths
```

Create `.cursor/rules/nuvel-commits.md`:
```markdown
# Nuvel Commit Conventions

## Commit Message Format
<type>(<scope>): <description>

[optional body with OrgMemory references]

Types: feat, fix, refactor, docs, test, chore, perf

## OrgMemory Integration
- Reference relevant ADRs in commit body when architectural changes are made
- Example: "Refs ADR-042 (event-driven communication pattern)"
```

### 5. Verify MCP Connection

In Cursor's AI chat panel (Cmd+L / Ctrl+L), ask:

```
List the available MCP tools. Specifically confirm that nuvel-orgmemory is connected and working.
```

Then test OrgMemory access:

```
Search nuvel-orgmemory for our coding standards for [language]. Summarize the key rules.
```

### 6. Configure Keyboard Shortcuts (Optional)

Recommended shortcuts for Nuvel workflows:

- **Cmd+Shift+N** — Nuvel context: Ask Cursor to query OrgMemory for task context
- **Cmd+Shift+R** — Nuvel review: Run OrgMemory-standards code review on current file
- **Cmd+Shift+U** — Nuvel update: Prompt to update OrgMemory with current changes

Configure in **Cursor Settings > Keyboard Shortcuts**.

## Pitfalls
- **MCP server not starting**: If `@nuvel/mcp-server-orgmemory` is not found, install it globally: `npm install -g @nuvel/mcp-server-orgmemory`. Or use a full path in `mcp.json`.
- **Environment variables in Cursor**: Cursor may not inherit all shell environment variables. Use the `env` field in `mcp.json` for critical vars, or launch Cursor from a terminal where vars are set.
- **.cursorrules conflicts**: If different team members modify `.cursorrules`, merge carefully. Consider keeping core Nuvel rules and letting individuals extend locally.
- **Indexing performance**: Large `.cursorignore` exclusions improve Cursor performance. Add build artifacts and large data directories.

## Verification
1. MCP server `nuvel-orgmemory` appears in Cursor's MCP settings with status "Connected"
2. Asking Cursor AI to list MCP tools shows nuvel-orgmemory tools
3. `.cursorrules` is present in project root with Nuvel directives
4. `.cursorignore` excludes common non-source directories
5. Cursor AI responses reference OrgMemory context when queried