---
name: cc-setup
description: Install, authenticate, and configure Anthropic Claude Code CLI for Nuvel-integrated development workflows.
---

# Claude Code CLI Setup for Nuvel

## Trigger Conditions
- First-time Claude Code CLI installation
- Setting up a new development environment
- Configuring Claude Code to work with Nuvel OrgMemory
- Migrating from another AI coding tool to Claude Code

## Prerequisites
- Node.js 18+ or the latest LTS
- An Anthropic API key (console.anthropic.com) or Claude subscription
- A Nuvel account with OrgMemory access (https://nuvel.dev)
- Git installed and configured

## Steps

### 1. Install Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
```

Verify installation:
```bash
claude --version
```
Expected output: `Claude Code v<version>`

### 2. Authenticate

**Option A: API Key (recommended for teams)**
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
claude auth --api-key
```

**Option B: OAuth (for individual Claude subscribers)**
```bash
claude login
```
This opens a browser for OAuth flow with your Anthropic account.

Verify authentication:
```bash
claude whoami
```

### 3. Configure Claude Code for Nuvel

Create `~/.claude/settings.json`:
```json
{
  "model": "claude-sonnet-4-20250514",
  "permissions": {
    "allow": [
      "Bash(git:*)",
      "Bash(npm:*)",
      "Bash(yarn:*)",
      "Bash(python:*)",
      "Bash(gh:*)",
      "Read",
      "Write",
      "WebSearch",
      "WebFetch"
    ],
    "deny": [
      "Bash(rm:-rf)",
      "Bash(sudo:*)"
    ]
  },
  "mcp_servers": {
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

Set environment variables:
```bash
export NUVEL_API_KEY="nv-..."
export NUVEL_ORG_ID="org_..."

# Persist in shell profile
cat >> ~/.bashrc << 'EOF'
export NUVEL_API_KEY="nv-..."
export NUVEL_ORG_ID="org_..."
EOF
```

### 4. Configure Hooks for Nuvel Integration

Create `~/.claude/hooks.json`:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write",
        "hooks": [
          {
            "type": "command",
            "command": "npx @nuvel/claude-code-hook check-patterns"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash(git:commit:*)",
        "hooks": [
          {
            "type": "command",
            "command": "npx @nuvel/claude-code-hook update-orgmemory --auto"
          }
        ]
      }
    ],
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "npx @nuvel/claude-code-hook heartbeat"
          }
        ]
      }
    ]
  }
}
```

### 5. Set Up CLAUDE.md with Nuvel Instructions

In your project root, create or append to `CLAUDE.md`:
```markdown
## Nuvel Integration
- Use the nuvel-orgmemory MCP server for team knowledge and decisions
- Check OrgMemory before implementing new features to avoid reinventing patterns
- After completing tasks, persist architectural decisions and learnings to OrgMemory
- Follow coding standards documented in OrgMemory > Coding Standards
```

### 6. Verify Configuration

```bash
# Check MCP server connectivity
claude -p "List available MCP tools and confirm nuvel-orgmemory is connected"

# Test end-to-end workflow
claude -p "Search nuvel-orgmemory for recent architecture decisions and summarize them"
```

## Pitfalls
- **API key format**: Anthropic API keys use the `sk-ant-` prefix. Ensure the full key including prefix is set.
- **MCP stdio transport**: The Nuvel MCP server uses stdio transport. Ensure `npx` can resolve `@nuvel/mcp-server-orgmemory` (may need `npm install -g @nuvel/mcp-server-orgmemory` first).
- **Settings.json path**: Claude Code looks for `~/.claude/settings.json` (user-level) and `.claude/settings.json` (project-level). Project-level settings override user-level.
- **Hook failures**: If a hook script fails with a non-zero exit code, Claude Code may abort the operation. Test hooks with `npx @nuvel/claude-code-hook check-patterns --dry-run` first.

## Verification
1. `claude --version` returns a valid version
2. `claude whoami` shows authenticated account
3. `claude -p "echo setup-verified"` completes without errors
4. MCP tools from nuvel-orgmemory appear in `claude -p "List your MCP tools"`
5. CLAUDE.md contains Nuvel integration instructions