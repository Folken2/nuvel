# Nuvel Connection Guide for GrokBot

## Overview

GrokBot (web/app/API) does not natively support MCP servers, so it cannot directly query Nuvel OrgMemory. This guide explains how to effectively use Grok with Nuvel through API integration and manual context sharing.

## Integration Approaches

### Approach 1: API Integration (Recommended for Automation)

Grok's OpenAI-compatible API enables programmatic Nuvel integration:

```
┌─────────────┐     API Client        ┌──────────────┐
│   Nuvel     │ ─── (manual fetch) ──→ │    Grok      │
│  OrgMemory  │                        │     API      │
└─────────────┘                        └──────────────┘
                                               │
                                               ▼
                                      ┌──────────────┐
                                      │  Generated    │
                                      │  Code/Design  │
                                      └──────────────┘
```

Use the Python/Node.js clients from `grok-config.md` to:
1. Fetch OrgMemory context before calling Grok API
2. Pass context as system prompt or prepended to user message
3. Process Grok's response and update OrgMemory

### Approach 2: Web Interface (Quick Tasks)

For ad-hoc tasks in the Grok web interface (grok.com):
1. Open Nuvel in a separate tab
2. Copy relevant ADRs, standards, or component docs
3. Paste into Grok with your prompt
4. Manually record outcomes in OrgMemory

## DeepSearch + OrgMemory Pattern

Grok's DeepSearch is powerful for architecture research:

```python
# Research workflow: DeepSearch + OrgMemory context
orgmemory_context = fetch_from_orgmemory("architecture decisions about messaging")
research_prompt = f"""
## Architecture Research

### OrgMemory Context
{orgmemory_context}

### Research Question
What is the best approach for [decision] given our current architecture?

### Deliverables
1. Comparison of approaches
2. Recommendation aligned with existing ADRs
3. Draft ADR for the chosen approach
"""

response = client.chat.completions.create(
    model="grok-3-deepsearch",
    messages=[{"role": "user", "content": research_prompt}],
)
```

## Limitations

| Feature | GrokBot | Workaround |
|---------|---------|------------|
| Direct OrgMemory query | ❌ Not supported | Fetch context before API call |
| MCP server integration | ❌ Not supported | Use Codex CLI or Claude Code CLI |
| Automated OrgMemory writes | ❌ Not supported | Write back to OrgMemory after Grok response |
| Image analysis | ✅ Supported | Use for architecture diagram review |

## When to Use Grok vs. Other Tools

| Task | Grok | Codex CLI | Claude Code CLI |
|------|------|-----------|-----------------|
| DeepResearch tasks | ✅ Best | ❌ N/A | ⚠️ Limited |
| Code generation | ✅ Good | ✅ Best | ✅ Best |
| Architecture analysis | ✅ Best | ⚠️ OK | ✅ Good |
| Direct file editing | ❌ N/A | ✅ Best | ✅ Best |
| MCP/OrgMemory automation | ❌ N/A | ✅ Best | ✅ Best |
| Image analysis (diagrams) | ✅ Best | ❌ N/A | ⚠️ Limited |

## Quick Start

1. Configure Grok API: See `grok-config.md`
2. Set up Nuvel context: See `../skills/grok-setup/SKILL.md`
3. Use Nuvel prompt templates: See `../skills/grok-workflows/SKILL.md`