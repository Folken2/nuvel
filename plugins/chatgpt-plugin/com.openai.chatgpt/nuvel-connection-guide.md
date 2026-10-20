# Nuvel Connection Guide for ChatGPT

## Overview

ChatGPT (web/app) does not natively support MCP servers, so it cannot directly query Nuvel OrgMemory. This guide explains how to effectively use ChatGPT with Nuvel through manual context sharing.

## How It Works

```
┌─────────────┐     Manual Context      ┌──────────────┐
│   Nuvel     │ ─── copy/paste ADRs, ──→ │   ChatGPT    │
│  OrgMemory  │     standards, docs      │  (Web/App)   │
└─────────────┘                          └──────────────┘
                                                  │
                                                  ▼
                                         ┌──────────────┐
                                         │  Generated    │
                                         │  Code/Design  │
                                         └──────────────┘
                                                  │
                                                  ▼
┌─────────────┐     After implementation  ┌──────────────┐
│   Nuvel     │ ←── record new ADRs, ──── │   Developer  │
│  OrgMemory  │     update docs           │              │
└─────────────┘                           └──────────────┘
```

## Workflow

### 1. Gather Context from OrgMemory

Before starting a ChatGPT session:
1. Open Nuvel (https://nuvel.dev)
2. Navigate to the relevant OrgMemory section
3. Copy architecture decisions, standards, and component docs

### 2. Provide Context to ChatGPT

Use the prompt template:
```
## OrgMemory Context

### Architecture Decisions
[paste from Nuvel]

### Coding Standards
[paste from Nuvel]

### Component Documentation
[paste from Nuvel]

## Task
[describe what you need]
```

### 3. Implement and Record

After ChatGPT provides a solution:
1. Implement the code in your project
2. Record any new decisions, patterns, or learnings in OrgMemory
3. Update component documentation if it changed

## Limitations

| Feature | ChatGPT | Workaround |
|---------|---------|------------|
| Direct OrgMemory query | ❌ Not supported | Copy/paste context manually |
| Automated OrgMemory updates | ❌ Not supported | Update OrgMemory manually after session |
| MCP server integration | ❌ Not supported | Use Codex CLI or Claude Code CLI for automated access |
| Context persistence | ❌ Session-only | Save important outputs to OrgMemory for future reference |

## When to Use ChatGPT vs. CLI Tools

| Task | ChatGPT (Web) | Codex CLI | Claude Code CLI |
|------|---------------|-----------|-----------------|
| Quick questions | ✅ Best | Overkill | Overkill |
| Code generation with standards | ✅ Good | ✅ Best | ✅ Best |
| Architecture discussion | ✅ Best | ⚠️ OK | ✅ Good |
| Automated OrgMemory access | ❌ N/A | ✅ Best | ✅ Best |
| PR creation and review | ❌ N/A | ✅ Best | ✅ Best |
| Multi-file refactors | ❌ N/A | ✅ Best | ✅ Best |

## Quick Start

1. Configure ChatGPT with Nuvel context: See `../skills/chatgpt-setup/SKILL.md`
2. Use Nuvel prompt templates: See `../skills/chatgpt-workflows/SKILL.md`
3. For automated workflows: Use the Codex or Claude Code plugin instead