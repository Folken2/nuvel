# Nuvel Roadmap

> **Thin harness, thick skills.**
>
> Layers, ownership and non-goals: see [VISION.md](VISION.md).

The roadmap is organised into technical phases. Each phase lists its deliverables.

---

## Phase 1 — Gateway integration

| Deliverable | Detail |
|---|---|
| **`--with-litellm` overlay** | Two-tier `Agent → LiteLLM → Provider` architecture for generated ADK agents: a LiteLLM proxy config, Dockerfile and compose file stamped alongside the agent. |
| **Gateway deployment** | A standalone LiteLLM instance backed by Postgres, with a virtual key per runtime, a restart policy and a documented bypass. |
| **Hermes through the gateway** | Hermes configured against the gateway as a custom OpenAI-compatible endpoint — configuration, not code. |
| **Execution-layer integration** | Background coding agents run in an external cloud-workspace runtime with all inference routed through the gateway, instead of a nuvel-built runtime. |

## Phase 2 — Skills and memory

| Deliverable | Detail |
|---|---|
| **Skill feedback loop** | An agent that hits an edge case proposes a skill patch as a pull request against the skill source, so skills improve from use. |
| **Gateway-backed cost attribution** | Spend per agent and per skill read from the gateway's spend logs, not from nuvel's own instrumentation. |
| **Cross-harness skill portability** | Every bundled skill is classified as portable or harness-specific, and portable skills are verified in each supported harness. |
| **Shared organisational memory** | Wire the existing `OrgMemoryService` (`nuvel/memory/org_memory_service.py`) so agents read and write one shared memory store. |

## Phase 3 — Distribution and auditability

| Deliverable | Detail |
|---|---|
| **Skill sharing and discovery** | Publish, version and discover skills from shared sources beyond the bundled set. |
| **Audit and access control** | Agent-layer audit trails and role-based access control, built on the trace stack. |

## Not on the roadmap

Recorded here so the decision is visible rather than repeated (see [VISION.md](VISION.md#non-goals)):

- **An agent runtime or cloud workspaces** — provided by the execution layer
- **Model routing and a provider abstraction** — provided by LiteLLM
- **Model-layer telemetry** — cost, tokens, latency and key attribution belong at the gateway
- **Breadth-first harness packaging** — a harness is added for a concrete technical need
