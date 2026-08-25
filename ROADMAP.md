# Nuvel Roadmap — August 2026

> **Thin harness, thick skills.** The leverage is not in the model weights — it's in how you wire the work.

---

## Near-term (this sprint)

| Priority | What | Why |
|---|---|---|
| 1 | **Design partner demo** | Draft their actual constitution + manifest, do a live deploy. Design partner is the fastest way to find real gaps. |
| 2 | **Skill evaluation dashboard** | `evalv2` exists but lives in CLI. A web view showing "these 5 skills are green, these 2 regressed" makes it a product. |
| 3 | **Co-assistant wiring** | Connect the fleet CLI to the SaaS frontend. `nuvel fleet deploy` → visible in a web UI. |

## Medium-term

| What | Why |
|---|---|
| **Skill feedback loop** | Skills should improve from use. Agent hits an edge case → proposes a skill patch → PR to the hub. The skill gets better every time it's used. |
| **Cross-harness audit** | Every skill must work in Claude Code, Cursor, Codex, Buzz, Hermes. The hub has 117 skills — need to know which ones are portable and which have harness-specific code. |
| **Company Brain** | `OrgMemoryService` is built. Wiring it so every fleet bot reads/writes the same company memory is the next step. "The fleet remembers." |

## Long-term

| What | Why |
|---|---|
| **Skill marketplace** | Users share and discover skills across companies. Network effects. |
| **Agent Directory** | `agentdirectory.folch.ai` — showcase live fleets and their skills |
| **Enterprise compliance** | Audit trails, RBAC, SOC2 — the things that make procurement say yes |

|## Ship mantra

> *"Abundance is not a policy paper. It is shipped software."*

---

## ═══ Reference: Airbyte Connector Skills (Aug 2026) ═══

[Full article](https://airbyte.com/blog/connector-skills-airbyte-agents) — Airbyte uses server-side skills (auto-generated connector docs + expert playbooks) to teach agents what a system can do and how to do the job right. **This is our thin-harness/thick-skills thesis validated by a production platform.**

### Stealable patterns

| Pattern | What Airbyte does | What it means for us |
|---------|------------------|---------------------|
| **Progressive disclosure** | Agent reads an *outline* first (few hundred tokens), then drills into specific sections. Never pages through irrelevant docs. | Our skills already have frontmatter + body. Add a `section` query param to `read_skill` so agents can read specific sections without loading the whole file. |
| **Live-state awareness** | Connector docs built from live sync state, not a static file. If a capability isn't ready, it doesn't appear. | Skills should be aware of what's actually configured. A Slack skill that knows which channels exist. A Jira skill that knows which projects. |
| **Template resolvers** | `{{ fiscal_year_summary }}` replaced per-organization by a resolver function before the skill is read. | Our `OrgMemoryService` can serve the same role: resolve placeholders from company memory before the agent sees the skill. |
| **Provenance fencing** | Machine-discovered values wrapped in `[begin customer-specific data (machine-discovered, unverified)]` markers. | Any value from agent discovery or user input that goes into a skill context should be provenance-fenced, not treated as instructions. |
| **Skill requirements** | `requirements.connectors: [salesforce]` gates visibility — skill only appears when the org has Salesforce. | Our skills should declare the connectors/data-sources they need, and the hub should filter them per fleet. |
| **One skill system, all surfaces** | Same server-side skills work in web, MCP, CLI, SDK. Improvements propagate instantly — no redeploy, no client update. | Our Skills MCP server already does this (`nuvel mcp serve`). Double down: every surface reads from the same hub. |
| **Skill functions** | Schema-validated server-side code codifying complex procedures. Agent declares the function, server executes it atomically. | Beyond skills-as-docs: skills-as-executable-routines. Our `scripts/` bundle in skills is the seed — make them callable, not just readable. |
| **Agent-authored skills** | Upcoming: orgs write their own skills in markdown+frontmatter, served through the same tooling. | Our skills hub already supports this. The feedback loop (agent proposes skill patch → PR to hub) is the killer feature. |

### Key quotes that validate our approach

> *"A skill is a document of working knowledge. It's a way to teach an agent how to accomplish various tasks such as querying a system well, computing metrics correctly, and making the right checks before writing data."*

> *"Giving an agent the right tools to access all the required systems solves only part of the problem. The agent also needs to know what can be done and how."*

> *"Implementing server-side skills as tools let us cover every surface with one mechanism and get consistent agent behavior across all of them."*

> *"Progressive disclosure is key. Simple agent tools can be annotated with a docstring... but after a certain level of complexity this approach breaks down."*

### Next actions for us

1. **Add section-scoped skill reading** — `read_skill(name, section="...")` so agents pay only for what they need
2. **Build skill requirements system** — `requires: [salesforce_connected, api_key_configured]` in frontmatter
3. **Implement provenance fencing** for `OrgMemoryService`-resolved values
4. **Evaluate skill functions** — can our `scripts/` bundles be declared callable functions?