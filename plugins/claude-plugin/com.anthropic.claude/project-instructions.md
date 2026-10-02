# Nuvel Project Instructions for Claude

## Project Context

This project uses **Nuvel** (nuvel.dev) for team knowledge management via **OrgMemory** — a Semantica-based knowledge graph containing:
- Architecture Decision Records (ADRs)
- Coding standards and conventions
- Component documentation and API specs
- Past incident reports and root cause analyses

## How to Use This Context

When I share OrgMemory context in our conversation:
1. **Ground your answers** in the provided architecture decisions and standards
2. **Flag deviations** when your suggestion doesn't align with documented patterns
3. **Reference ADRs** by their ID (e.g., ADR-042) when relevant

## Project Overview

<!-- Fill in your project details below -->
- **Project:** [Project Name]
- **Tech Stack:** [Languages/Frameworks]
- **Architecture:** [Brief architecture description]
- **Team Size:** [Number of engineers]

## Key Conventions (from OrgMemory)

<!-- Copy your key conventions from OrgMemory -->
- [Convention 1]
- [Convention 2]
- [Convention 3]

## OrgMemory Links

- **Architecture Decisions:** https://nuvel.dev/org/[org-id]/decisions
- **Coding Standards:** https://nuvel.dev/org/[org-id]/standards
- **Component Docs:** https://nuvel.dev/org/[org-id]/components
- **Incident History:** https://nuvel.dev/org/[org-id]/incidents

## Response Expectations

When I ask for technical help:
1. If I provided OrgMemory context, use it as the foundation for your answer
2. If I haven't provided context, ask if there are relevant OrgMemory entries
3. Structure complex answers as: Context Check → Solution → Trade-offs → Next Steps
4. Always note when a new ADR should be created in OrgMemory

## Updating This File

This file should be updated when:
- Major architecture decisions are made (add reference to new ADRs)
- Team conventions change (update Key Conventions section)
- Project tech stack changes

Update frequency: Review monthly and after each significant architectural change.

---

*This project instructions file is part of the claude-plugin for Nuvel integration.*
*See https://github.com/folken2/claude-plugin for updates.*