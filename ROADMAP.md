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

## Ship mantra

> *"Abundance is not a policy paper. It is shipped software."*