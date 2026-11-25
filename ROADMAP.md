# Nuvel Roadmap

> **Tested machinery, thick skills.**
>
> Layers, ownership and non-goals: see [VISION.md](VISION.md).

The roadmap is organised into technical phases. Each phase lists its deliverables. Shipped
deliverables move to the end of this file.

---

## Phase 1: Generated agents on nuvel-harness

Tracked in [#168](https://github.com/Folken2/nuvel/issues/168).

| Deliverable | Detail |
|---|---|
| **Module map** | Every module in the ADK template marked as replaced by a harness equivalent, kept as agent code, or dropped. |
| **Harness dependency** | The template's `requirements.txt` pins [nuvel-harness](https://github.com/Folken2/nuvel-harness), and the template image moves to the Python version the harness needs. |
| **Migration in slices** | Plugins, then guardrails, then the runner, each slice green on the scaffold tests and on a freshly generated agent that starts and answers a turn. |
| **Skills follow** | The `adk-*` skills and the repo's agent skill describe the harness pieces, not the old template modules. |

## Phase 2: Gateway integration

| Deliverable | Detail |
|---|---|
| **Gateway deployment** | A standalone LiteLLM instance backed by Postgres, with a virtual key per runtime, a restart policy and a documented bypass ([#89](https://github.com/Folken2/nuvel/issues/89)). |
| **Harness routes through the gateway** | The harness's per-turn routes point at the gateway, so retry and fallback keep their agent-level knowledge while the gateway keeps keys and budgets. |
| **Hermes through the gateway** | Hermes configured against the gateway as a custom OpenAI-compatible endpoint: configuration, not code. |
| **Execution-layer integration** | Background coding agents run in an external cloud-workspace runtime with all inference routed through the gateway. |

## Phase 3: Skills and memory

| Deliverable | Detail |
|---|---|
| **Skill feedback loop** | An agent that hits an edge case proposes a skill patch as a pull request against the skill source, so skills improve from use. Today `propose_improvement` files the proposal as an issue. |
| **Cost per agent and per skill** | The harness ledger attributes spend to the run, turn and tool. The gateway's spend logs are the wire-level check against it. |
| **Cross-framework skill portability** | Every bundled skill is classified as portable or framework-specific, and portable skills are verified in each supported framework. |

## Phase 4: Distribution and auditability

| Deliverable | Detail |
|---|---|
| **Skill sharing and discovery** | Publish, version and discover skills from shared sources beyond the bundled set and the Nuvel Skills hub. |
| **Audit and access control** | Agent-layer audit trails and role-based access control, built on the trace stack and the harness ledger. |

---

## Not on the roadmap

Recorded here so the decision is visible rather than repeated (see [VISION.md](VISION.md#non-goals)):

- **Cloud workspaces**: provided by the execution layer
- **A provider abstraction**: provided by LiteLLM
- **Wire-level telemetry**: key attribution, budgets and per-provider spend belong at the gateway
- **Breadth-first framework packaging**: a framework backend is added for a concrete technical need
- **A second copy of the runtime**: machinery every agent needs lives in nuvel-harness

## Shipped

| Deliverable | Where |
|---|---|
| **`--with-litellm` overlay** | `Agent → LiteLLM → Provider` for generated ADK agents: proxy config, Dockerfile and compose file stamped alongside the agent. |
| **Shared organisational memory** | `OrgMemoryService` wired into generated ADK agents when `NUVEL_ORG_MEMORY_DSN` and `NUVEL_ORG_GRAPH_PATH` are set, and into the meta-agent through `NUVEL_ORG_MEMORY_URI` (0.3.0, [#54](https://github.com/Folken2/nuvel/pull/54)). |
