# Nuvel Connection Guide for Claude (Web/App)

## Overview

Claude (web/app at claude.ai) does not natively support MCP servers, so it cannot directly query Nuvel OrgMemory. This guide explains how to effectively use Claude with Nuvel through manual context sharing and project knowledge.

## How It Works

```
┌─────────────┐     Project Knowledge     ┌──────────────┐
│   Nuvel     │ ─── + Session Context ───→ │    Claude    │
│  OrgMemory  │     (manual copy/paste)    │  (Web/App)   │
└─────────────┘                            └──────────────┘
                                                    │
                                                    ▼
                                           ┌──────────────┐
                                           │  Generated    │
                                           │  Code/Design  │
                                           └──────────────┘
                                                    │
                                                    ▼
┌─────────────┐     After implementation    ┌──────────────┐
│   Nuvel     │ ←── record new ADRs, ────── │   Developer  │
│  OrgMemory  │     update docs             │              │
└─────────────┘                             └──────────────┘
```

## Workflow Options

### Option A: Claude Projects (Recommended)

1. Create a Claude Project for your codebase
2. Upload `project-instructions.md` as project knowledge
3. Start conversations within the project for automatic context

### Option B: Session Context (Quick Start)

At the start of each session, paste:
```
Context: Working on [project], using Nuvel OrgMemory for architecture and standards.
I'll share relevant OrgMemory context. Ground answers in it when provided.
```

### Option C: Custom Instructions

Set in Claude Settings for persistent guidelines:
- Reference Nuvel when appropriate
- Ask for OrgMemory context if not provided
- Flag deviations from team standards

## Limitations

| Feature | Claude (Web) | Workaround |
|---------|-------------|------------|
| Direct OrgMemory query | ❌ Not supported | Copy/paste context from Nuvel |
| MCP server integration | ❌ Not supported | Use Claude Code CLI for MCP access |
| Automated OrgMemory updates | ❌ Not supported | Update OrgMemory manually |
| File system access | ❌ Not supported | Use Claude Code CLI for file ops |

## When to Use Claude (Web) vs. Claude Code CLI

| Task | Claude (Web) | Claude Code CLI |
|------|-------------|-----------------|
| Architecture discussions | ✅ Best | ⚠️ OK |
| Code generation with context | ✅ Good | ✅ Best |
| Research and analysis | ✅ Best | ⚠️ OK |
| Direct file editing | ❌ N/A | ✅ Best |
| Automated OrgMemory access | ❌ N/A | ✅ Best |
| Running tests | ❌ N/A | ✅ Best |

## Quick Start

1. Configure Claude with Nuvel context: See `../skills/claude-setup/SKILL.md`
2. Use Nuvel-aligned prompt patterns: See `../skills/claude-workflows/SKILL.md`
3. For file-level automation: Use the Claude Code CLI plugin instead