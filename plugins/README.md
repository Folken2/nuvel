# Nuvel Agent Plugins

Agent Plugins (agent-plugins.org v1.0.0) for integrating [Nuvel](https://nuvel.dev) OrgMemory into major AI coding and chat harnesses.

## Overview

Nuvel OrgMemory is a Semantica-based knowledge graph that stores your team's architecture decisions, coding standards, component documentation, and past incident reports. These plugins make OrgMemory accessible from within your AI coding tools — giving AI assistants context-aware understanding of your team's patterns and decisions.

## Plugins

| Plugin | Harness | MCP Support | Skills |
|--------|---------|-------------|--------|
| [codex-plugin](./codex-plugin/) | OpenAI Codex CLI | ✅ streamable-http | setup, agent, review |
| [claude-code-plugin](./claude-code-plugin/) | Anthropic Claude Code CLI | ✅ stdio | setup, agent, review |
| [chatgpt-plugin](./chatgpt-plugin/) | OpenAI ChatGPT (web/app) | ❌ | setup, workflows |
| [claude-plugin](./claude-plugin/) | Anthropic Claude (web/app) | ❌ | setup, workflows |
| [grokbot-plugin](./grokbot-plugin/) | xAI GrokBot (web/app/API) | ❌ | setup, workflows |
| [cursor-plugin](./cursor-plugin/) | Cursor IDE | ✅ stdio | setup, agent, review |

### MCP Support

Three plugins include direct MCP server integration for automated OrgMemory access:
- **codex-plugin** — streamable-http transport to Nuvel API
- **claude-code-plugin** — stdio transport via `@nuvel/mcp-server-orgmemory`
- **cursor-plugin** — stdio transport via `@nuvel/mcp-server-orgmemory`

Plugins without MCP support (chatgpt, claude, grokbot) use manual context sharing — copy/paste OrgMemory entries into prompts. Use the CLI-based plugins for fully automated workflows.

## Quick Start

### 1. Choose Your Plugin

- **For autonomous coding tasks** (features, PRs, refactors): [codex-plugin](./codex-plugin/) or [claude-code-plugin](./claude-code-plugin/)
- **For IDE-integrated development**: [cursor-plugin](./cursor-plugin/)
- **For web/app-based chat**: [chatgpt-plugin](./chatgpt-plugin/), [claude-plugin](./claude-plugin/), or [grokbot-plugin](./grokbot-plugin/)

### 2. Get Your Nuvel Credentials

```bash
# Get your API key from https://nuvel.dev/settings/api
export NUVEL_API_KEY="nv-..."
export NUVEL_ORG_ID="org_..."
```

### 3. Install a Plugin

Each plugin is self-contained. Copy the plugin directory or install via agent-plugins.org registry:

```bash
# Example: Install Claude Code plugin
git clone https://github.com/folken2/claude-code-plugin.git
cd claude-code-plugin
# Follow the setup skill: skills/cc-setup/SKILL.md
```

### 4. Verify

After setup, test OrgMemory access from your harness:

```
# In Claude Code CLI
claude -p "Search OrgMemory for our coding standards"

# In Codex CLI  
codex exec "List the Nuvel OrgMemory MCP tools available"

# In Cursor Chat (Cmd+L)
"Search nuvel-orgmemory for recent architecture decisions"
```

## Plugin Structure

Each plugin follows the [Agent Plugins v1.0.0](https://agent-plugins.org) standard:

```
<name>-plugin/
├── plugin.json          # Plugin metadata (name, version, author, etc.)
├── skills/              # Reusable skill workflows
│   └── <skill-name>/
│       └── SKILL.md     # Step-by-step instructions with pitfalls and verification
├── mcp.json             # MCP server configuration (where applicable)
└── <client-namespace>/  # Harness-specific config files
```

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                   AI Coding Harness                    │
│  ┌─────────┐  ┌──────────┐  ┌──────┐  ┌───────────┐  │
│  │  Codex  │  │Claude Code│  │Cursor│  │ChatGPT etc│  │
│  └────┬────┘  └────┬─────┘  └──┬───┘  └─────┬─────┘  │
│       │            │           │             │         │
│       ▼            ▼           ▼             ▼         │
│  ┌────────────────────────────────────────────────┐   │
│  │         Nuvel Plugin (MCP / Manual)            │   │
│  │  ┌──────────────────────────────────────────┐  │   │
│  │  │        OrgMemory Knowledge Graph          │  │   │
│  │  │  ADRs │ Standards │ Components │ Incidents│  │   │
│  │  └──────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## Skill Workflows

Each plugin provides skills covering common development workflows:

- **setup** — Install, authenticate, and configure the harness with Nuvel
- **agent** — Run autonomous coding tasks (features, fixes, refactors)
- **review** — Automated code review against OrgMemory standards
- **workflows** — Prompt templates and patterns for manual context sharing

## Contributing

Each plugin lives in its own repository:
- https://github.com/folken2/codex-plugin
- https://github.com/folken2/claude-code-plugin
- https://github.com/folken2/chatgpt-plugin
- https://github.com/folken2/claude-plugin
- https://github.com/folken2/grokbot-plugin
- https://github.com/folken2/cursor-plugin

See each plugin's SKILL.md files for contribution guidelines specific to that harness.

## License

All plugins: MIT License

## Support

- Nuvel: https://nuvel.dev
- Agent Plugins: https://agent-plugins.org
- Contact: mark@folch.ai