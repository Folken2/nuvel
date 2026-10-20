# Nuvel Roadmap — October 2026

> **Thin harness, thick skills.** The leverage is not in the model weights — it's in how you wire the work.
>
> Strategy and layer ownership: see [VISION.md](VISION.md).

---

## Near-term (this sprint)

| Priority | What | Why |
|---|---|---|
| 1 | **Stand up the LiteLLM gateway** | Own instance, Postgres, virtual keys per runtime. It governs every agent we run, so it comes first. |
| 2 | **Point Hermes at the gateway** | Config, not code — a custom OpenAI-compatible endpoint. Gets our own calls traced and attributed from day one. |
| 3 | **Land the `--with-litellm` overlay** | The two-tier `Agent → LiteLLM → Provider` architecture is written and CI-green. It was an open question last week; it is now the critical path. |
| 4 | **Evaluate Moyai for background coding** | Self-hosted, six harnesses including Hermes, all inference through LiteLLM. Replaces the "build our own runtime" ambition. |

## Medium-term

| What | Why |
|---|---|
| **Skill feedback loop** | Skills should improve from use. Agent hits an edge case → proposes a skill patch → PR to the hub. The skill gets better every time it's used. |
| **Gateway-backed cost attribution** | Spend per customer, per agent, per skill — from the gateway, not from our own instrumentation. Makes "what did this agent cost me" a number we can hand a customer. |
| **Cross-harness skill portability** | Every skill must work in the harnesses that matter. Know which are portable and which carry harness-specific code. Scoped to real customer need, not breadth for its own sake. |
| **Company Brain** | `OrgMemoryService` is built. Wire it so every agent reads and writes the same organisational memory. "The fleet remembers." |

## Long-term

| What | Why |
|---|---|
| **Skill marketplace** | Users share and discover skills across companies. Network effects. |
| **Agent Directory** | `agentdirectory.folch.ai` — showcase live fleets and their skills |
| **Enterprise compliance** | Audit trails, RBAC, SOC2 — the things that make procurement say yes. Agent-layer evidence, which infrastructure vendors structurally cannot supply. |

## Explicitly not building

Held here so the decision is visible rather than repeated:

- **Our own agent runtime / cloud workspaces** — adopted (Moyai or equivalent)
- **Our own model routing and provider abstraction** — adopted (LiteLLM)
- **Our own model-layer telemetry** — cost, tokens, latency and key attribution belong at the gateway
- **Breadth-first harness packaging** — if a harness is not a customer requirement, it is not our surface

## Ship mantra

> *"Abundance is not a policy paper. It is shipped software."*
