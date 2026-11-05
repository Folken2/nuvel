# Nuvel Vision

> **Adopt the commodity layers. Build the authoring layer.**

This document describes what nuvel is, which layers it owns, how it is deployed alongside the
systems it depends on, and what it deliberately does not build.

---

## What nuvel is

nuvel is a toolkit for authoring production-grade AI agents across Google ADK (default), Hermes
Agent and the Claude Agent SDK. It consists of:

- **The scaffolder** — `nuvel agent create`, per-framework templates under
  `nuvel/backends/<framework>/templates/` and opt-in overlays under
  `nuvel/backends/adk/templates_overlays/`.
- **The skills** — versioned, composable, portable markdown skills in the Anthropic skills format
  under `nuvel/backends/<framework>/skills/`, served to other tools by
  [nuvel-mcp](https://github.com/Folken2/nuvel-mcp).
- **The meta-agent** — `nuvel run`, an ADK agent that scaffolds and iterates on agents from a
  natural-language description.
- **The agent-layer trace and eval stack** — `nuvel traces`, `nuvel eval`, `nuvel evalv2`.

---

## Layers and ownership

| Layer | Owner | What lives there |
|---|---|---|
| **Model** | [LiteLLM](https://github.com/BerriAI/litellm) | gateway, virtual keys, budgets, spend attribution, provider routing, model-layer traces |
| **Execution** | an external cloud-workspace runtime | isolated workspaces — terminal, filesystem, browser — for background coding agents |
| **Authoring** | **nuvel** | scaffolding, skills, templates, overlays, agent-layer traces, eval |

nuvel builds the authoring layer and integrates with the other two rather than reimplementing
them. The model and execution layers are general-purpose infrastructure maintained in the open;
the authoring layer is where an agent's process, gates and tools are encoded in a form that can
be reviewed and changed.

---

## Two trace layers — keep both, collapse neither

| Layer | Owner | Answers |
|---|---|---|
| **Model layer** | LiteLLM | Which provider, which key, how many tokens, what did it cost, did it hit a budget |
| **Agent layer** | nuvel (`nuvel traces`, `nuvel eval`) | Which turn, which tool, did the skill load, did the gate fire, was the outcome correct |

The gateway cannot tell you that a gate did not fire — that is agent semantics. nuvel cannot tell
you which virtual key paid for a call — that is wire-level.

**The gateway governs the wire. nuvel governs the agent.**

---

## Deployment topology

```
        ┌──────────────────────────────────────┐
        │           LiteLLM Gateway            │
        │  keys · budgets · spend · traces     │
        └──────────────────┬───────────────────┘
                           │  all inference
        ┌──────────────────┼───────────────────┐
        │                  │                   │
        ▼                  ▼                   ▼
     Hermes          nuvel-generated      cloud-workspace
  (orchestration)        agents               runtime
                                         (background coding)
```

- Generated ADK agents can ship a ready-to-run gateway with `nuvel agent create --with-litellm`,
  which routes the agent's LLM calls through a LiteLLM proxy instead of calling providers directly.
- The gateway is a **single point of failure for every runtime** behind it. It needs its own
  instance, a restart policy, and a documented bypass.
- It does **not** share a machine with anything it governs. A governance layer co-located with a
  governed component is not a governance layer.
- Its real sizing constraint is **spend-log storage**, not CPU: budget 10–20 KB per logged request
  when prompts are stored, and set retention accordingly.

---

## Non-goals

- **An agent runtime.** Cloud workspaces, harness abstraction, checkpointing and session resume
  belong to the execution layer.
- **Model routing or a provider abstraction.** LiteLLM already spans 100+ providers.
- **Model-layer telemetry.** Cost, tokens, latency and key attribution belong at the gateway.
- **Breadth-first harness packaging.** A harness is supported when there is a concrete technical
  need for it, not for coverage's sake.

---

## Design test for new work

Before starting anything, ask:

1. **Is this maintained in the open by another project?** If so, integrate with it.
2. **Is it agent-layer or model-layer?** Model-layer concerns go to the gateway.
3. **Does it encode how an agent does its work** — skills, gates, tools, evaluation? If not, it is
   probably not authoring-layer work.

> *Thin harness, thick skills.* The leverage is in how the work is wired, not in the model weights.
