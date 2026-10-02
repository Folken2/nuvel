---
name: chatgpt-setup
description: Configure OpenAI ChatGPT (web/app) with Nuvel instructions and custom GPT settings for integrated workflows.
---

# ChatGPT Setup for Nuvel

## Trigger Conditions
- Setting up ChatGPT for Nuvel-integrated development work
- Creating a custom GPT for team-specific tasks
- Configuring ChatGPT's system instructions for Nuvel context
- Onboarding a team member to use ChatGPT with Nuvel

## Prerequisites
- ChatGPT Plus, Team, or Enterprise subscription
- A Nuvel account with OrgMemory access (https://nuvel.dev)
- Access to the ChatGPT web interface or desktop app

## Steps

### 1. Configure Custom Instructions

In ChatGPT, go to **Settings > Personalization > Custom Instructions**.

**What would you like ChatGPT to know about you?**

Paste the following (customize bracketed values):
```
I am a software engineer working at [Company Name]. I use Nuvel (https://nuvel.dev) for team knowledge management and OrgMemory for architectural decisions and coding standards.

My primary tech stack: [languages/frameworks]
My role: [role, e.g., "Senior Backend Engineer"]
My team's domain: [domain, e.g., "payment processing", "developer tools"]
```

**How would you like ChatGPT to respond?**

Paste:
```
## Nuvel Integration Guidelines

Before answering implementation questions:
1. Ask if there are relevant Nuvel OrgMemory entries, team conventions, or architecture decisions that apply.
2. If OrgMemory references are provided, ground your answer in those existing decisions.
3. Flag when your suggestion would deviate from established patterns.

## Response Style
- Provide working code examples with proper error handling.
- Include references to relevant Nuvel OrgMemory entries when applicable.
- For architectural decisions, note that the final decision should be recorded in OrgMemory.
- Prefer patterns and libraries already used in the codebase (ask if unsure).

## Output Format
For coding tasks, structure responses as:
1. Context (OrgMemory references if applicable)
2. Solution (with code)
3. Trade-offs (what was considered, why this approach)
4. Next steps (testing, OrgMemory update, PR creation)
```

### 2. Create a Nuvel Custom GPT (Optional, Team/Enterprise)

For teams that want a shared GPT with Nuvel context:

1. Go to **Explore GPTs > Create**
2. Configure the GPT:

**Name:** Nuvel Dev Assistant

**Description:**
```
Software engineering assistant integrated with Nuvel OrgMemory. 
Provides context-aware coding help, code review, and architectural guidance 
based on your team's knowledge graph.
```

**Instructions:**
```
You are a software engineering assistant integrated with Nuvel (nuvel.dev), 
a team knowledge management platform with OrgMemory (Semantica-based knowledge graph).

OPERATING MODE:
1. When users ask coding questions, first ask if they can provide relevant OrgMemory 
   context (architecture decisions, coding standards, component docs).
2. If they provide OrgMemory context, ground your answers in those existing decisions.
3. When suggesting new patterns or architecture changes, remind users to record 
   the decision in OrgMemory after implementation.
4. For code review requests, ask for the relevant OrgMemory standards to check against.

CAPABILITIES:
- Code generation with OrgMemory-aware patterns
- Architecture review against documented decisions
- Bug analysis with context from past incidents
- Test generation following team testing conventions
- Documentation that links to OrgMemory entries

LIMITATIONS:
- You cannot directly access Nuvel OrgMemory. Always ask users to provide 
  the relevant context from OrgMemory before giving definitive answers.
- For tasks that require direct OrgMemory access, recommend using the 
  Codex CLI or Claude Code CLI with the Nuvel plugin instead.

Always maintain awareness that the user's team has established patterns 
and decisions in OrgMemory. Your suggestions should align with those 
whenever possible, and you should explicitly note when you're suggesting 
something new.
```

**Capabilities:** Enable Web Browsing and DALL·E Image Generation.

**Actions:** None (ChatGPT cannot directly call MCP servers yet; for direct OrgMemory access, use Codex or Claude Code CLI).

### 3. Use the Nuvel Prompt Template

For consistent interactions, use this template when starting a task:

```
## Task
[Describe what you need]

## OrgMemory Context
[Paste relevant entries from Nuvel OrgMemory]:
- Architecture Decision: [paste]
- Coding Standard: [paste]
- Component Docs: [paste]

## Constraints
- Must follow: [specific patterns from OrgMemory]
- Tech stack: [languages/frameworks]
- Files to modify: [paths]
```

### 4. Set Up Project-Specific Context

Create a project context file for quick copy-paste:

```bash
cat > .chatgpt/project-context.md << 'EOF'
# Project Context for ChatGPT

## Nuvel OrgMemory Links
- Architecture Decisions: https://nuvel.dev/org/[org-id]/decisions
- Coding Standards: https://nuvel.dev/org/[org-id]/standards
- Component Catalog: https://nuvel.dev/org/[org-id]/components

## Tech Stack
- Frontend: [React/Next.js/Vue/etc.]
- Backend: [Node.js/Python/Go/etc.]
- Database: [PostgreSQL/MongoDB/etc.]
- Infrastructure: [AWS/GCP/Vercel/etc.]

## Key Conventions
- [Convention 1]
- [Convention 2]
EOF
```

## Pitfalls
- **No direct OrgMemory access**: ChatGPT cannot query OrgMemory directly. Always provide relevant context manually or use Codex/Claude Code CLI for automated access.
- **Custom instructions length**: ChatGPT's custom instructions have a character limit (~1500 chars). Keep Nuvel instructions concise.
- **GPT actions limitation**: Custom GPTs cannot call external MCP servers. This is a platform limitation — use CLI tools for automated integration.
- **Session context loss**: ChatGPT sessions don't persist context between conversations. Save important outputs to OrgMemory manually.

## Verification
1. Custom instructions are active in ChatGPT settings
2. ChatGPT responses reference Nuvel and OrgMemory when context is provided
3. Outputs follow the structured format (Context → Solution → Trade-offs → Next steps)
4. Team members can access the shared Nuvel Dev Assistant GPT (if created)