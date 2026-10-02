---
name: codex-setup
description: Install, authenticate, and configure OpenAI Codex CLI for Nuvel-integrated development workflows.
---

# Codex CLI Setup for Nuvel

## Trigger Conditions
- First-time Codex CLI installation
- Need to authenticate with OpenAI API
- Setting up a new development machine with Codex
- Configuring Codex to work with Nuvel OrgMemory and project context

## Prerequisites
- Node.js 18+ or the latest LTS
- An OpenAI API key with Codex access (platform.openai.com)
- A Nuvel account with OrgMemory access (https://nuvel.dev)

## Steps

### 1. Install Codex CLI
```bash
npm install -g @openai/codex
```
Verify installation:
```bash
codex --version
```
Expected output: `@openai/codex <version>` (1.0.0 or later)

### 2. Authenticate with OpenAI
```bash
codex login
```
This opens a browser window. Log in with your OpenAI account that has Codex API access.

Alternatively, use an API key directly:
```bash
export OPENAI_API_KEY="sk-..."
codex auth --api-key
```

Verify authentication:
```bash
codex whoami
```

### 3. Configure Codex for Nuvel Integration

Create or edit `~/.codex/config.json`:
```json
{
  "model": "gpt-5",
  "approval_policy": "on-request",
  "mcp_servers": {
    "nuvel-orgmemory": {
      "type": "streamable-http",
      "url": "https://api.nuvel.dev/mcp/orgmemory",
      "headers": {
        "Authorization": "Bearer ${NUVEL_API_KEY}"
      }
    }
  }
}
```

Set your Nuvel API key:
```bash
export NUVEL_API_KEY="nv-..."
# Add to shell profile for persistence:
echo 'export NUVEL_API_KEY="nv-..."' >> ~/.bashrc
```

### 4. Set Up Nuvel Project Context

In your project root, create `.codex/instructions.md`:
```markdown
## Nuvel Integration
- Use the nuvel-orgmemory MCP server to access team knowledge
- Before implementing features, check OrgMemory for existing decisions and architecture docs
- After completing tasks, update OrgMemory with new learnings and decisions
```

### 5. Verify End-to-End Configuration
```bash
codex exec "List the MCP tools available from the nuvel-orgmemory server"
```

## Pitfalls
- **API key scope**: Ensure your OpenAI API key has Codex access. Standard API keys may not include it by default.
- **Node.js version**: Codex requires Node 18+. Use `nvm install 18` if needed.
- **MCP connection failures**: If OrgMemory MCP server is unreachable, check that `NUVEL_API_KEY` is exported and your Nuvel account is active.
- **Config path**: On Windows, use `%USERPROFILE%\.codex\config.json` instead of `~/.codex/config.json`.

## Verification
1. `codex --version` returns a valid version number
2. `codex whoami` shows your authenticated OpenAI account
3. `codex exec "echo setup-verified"` completes without errors
4. OrgMemory MCP tools appear in the available tools list