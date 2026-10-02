---
name: claude-setup
description: Configure Anthropic Claude (web/app) with Nuvel project context for integrated development workflows.
---

# Claude (Web/App) Setup for Nuvel

## Trigger Conditions
- Setting up Claude for Nuvel-integrated development
- Creating project-specific Claude configurations
- Onboarding team members to use Claude with Nuvel context
- Configuring Claude's project knowledge for OrgMemory-aware responses

## Prerequisites
- Claude Pro, Team, or Enterprise subscription
- A Nuvel account with OrgMemory access (https://nuvel.dev)
- Access to Claude web (claude.ai) or desktop app

## Steps

### 1. Configure Project Knowledge

**Option A: Using Claude Projects (Recommended)**

1. Go to **claude.ai > Projects > Create Project**
2. Name: `[Your Project] — Nuvel`
3. In **Project Knowledge**, upload or paste:

Create `claude-project-knowledge.md`:
```markdown
# Nuvel Integration for [Project Name]

## What is Nuvel?
Nuvel (nuvel.dev) is our team's knowledge management platform.
OrgMemory is our Semantica-based knowledge graph containing:
- Architecture Decision Records (ADRs)
- Coding standards and conventions
- Component documentation
- Past incident reports and root cause analyses

## How to Use OrgMemory Context
When I ask technical questions about our codebase:
1. I may provide relevant OrgMemory context — ground your answers in it.
2. If suggesting new patterns, note they should be recorded as ADRs.
3. For code reviews, I'll share relevant standards for you to check against.

## Project Overview
- **Tech Stack:** [languages/frameworks]
- **Architecture:** [high-level description]
- **Key Components:** [list main components]
- **Team:** [team size/roles]

## Key Conventions (from OrgMemory)
- [Convention 1]
- [Convention 2]
- [Convention 3]

## OrgMemory Links
- Architecture Decisions: https://nuvel.dev/org/[org-id]/decisions
- Coding Standards: https://nuvel.dev/org/[org-id]/standards
- Component Docs: https://nuvel.dev/org/[org-id]/components
```

4. Upload this as project knowledge.

**Option B: Per-Conversation Context**

If not using Projects, start each session by pasting:
```
Context: I work on [project] at [company]. We use Nuvel OrgMemory for 
architecture and standards. I'll share relevant context from it. 
Please ground answers in that context when provided.
```

### 2. Configure Custom Instructions

In **Claude > Settings > Personal Instructions** (or per-project instructions):

```
## Response Guidelines for Nuvel-Integrated Development

### When Context is Provided
- Ground answers in provided OrgMemory architecture decisions and standards.
- Flag when a suggestion would deviate from established patterns.
- Reference specific OrgMemory entries by name when applicable.

### Output Structure for Technical Tasks
For implementation requests, structure output as:
1. **Context Check**: Confirm alignment with OrgMemory standards
2. **Solution**: Implementation with code examples
3. **Trade-offs**: Alternatives considered, why this approach
4. **OrgMemory Actions**: What should be recorded after implementation

### Code Style
- Follow the conventions documented in our OrgMemory standards when provided.
- Include proper error handling and edge case consideration.
- Write production-quality code, not prototypes.
- Add comments explaining non-obvious decisions.

### Limitations
- You cannot directly access Nuvel OrgMemory. I will provide relevant context.
- For tasks requiring direct OrgMemory querying, I use Claude Code CLI with the Nuvel MCP server.
```

### 3. Create a Context Template

Save this template for quick use at session start:

```markdown
## Session Context

### Today's Task
[Describe the task]

### OrgMemory Context (from Nuvel)
**Relevant Architecture Decisions:**
[paste from OrgMemory]

**Applicable Coding Standards:**
[paste from OrgMemory]

**Related Components:**
[paste from OrgMemory]

### Constraints
- Must follow: [specific patterns]
- Files: [paths if relevant]
- Dependencies: [versions if pinned]

### Expected Output
[What you need from Claude]
```

### 4. Set Up Quick Access to OrgMemory

Create a browser bookmark or desktop shortcut to your OrgMemory dashboard:

**Bookmark:**
```
Name: Nuvel OrgMemory
URL: https://nuvel.dev/org/[org-id]
```

**Or create a Raycast/Alfred shortcut** (macOS):
```bash
open "https://nuvel.dev/org/$(echo $NUVEL_ORG_ID)"
```

Keep OrgMemory open in a side tab for quick context copy-paste during Claude sessions.

### 5. Integration with Claude Desktop App

If using the Claude desktop app:
1. Install from claude.ai/download
2. Sign in with your Anthropic account
3. The app shares project knowledge with web — configurations sync automatically

## Pitfalls
- **No MCP support**: Web Claude does not support MCP servers. For automated OrgMemory access, use Claude Code CLI (`claude-code-plugin`).
- **Project knowledge limits**: Claude Projects have file size limits. Keep project knowledge files concise and update them when OrgMemory changes significantly.
- **Context length**: Very long pasted OrgMemory entries may exceed Claude's context window. Summarize key points.
- **Stale project knowledge**: Update project knowledge files when team conventions or architecture changes. Set a calendar reminder to review monthly.

## Verification
1. Created a Claude Project with Nuvel context knowledge
2. Custom instructions include Nuvel/OrgMemory response guidelines
3. Session context template is accessible for quick copy-paste
4. Claude responses reference provided OrgMemory context
5. OrgMemory dashboard is easily accessible during sessions