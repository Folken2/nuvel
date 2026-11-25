# Nuvel Vision

> **Adopt the commodity layers. Build the authoring layer and the machinery every agent needs.**

This document describes what nuvel is, which layers it owns, how it is deployed alongside the
systems it depends on, and what it deliberately does not build.

---

## What nuvel is

nuvel is a toolkit for authoring production-grade AI agents across Google ADK (default), Hermes
Agent and the Claude Agent SDK. It consists of:

- **The scaffolder**: `nuvel agent create`, per-framework templates under
  `nuvel/backends/<framework>/templates/` and opt-in overlays under
  `nuvel/backends/adk/templates_overlays/`.
- **The skills**: versioned, composable, portable markdown skills in the Anthropic skills format
  under `nuvel/backends/<framework>/skills/`, served to other tools by
  [nuvel-mcp](https://github.com/Folken2/nuvel-mcp).
- **The meta-agent**: `nuvel run`, an ADK agent that scaffolds and iterates on agents from a
  natural-language description.
- **The agent-layer trace and eval stack**: `nuvel traces`, `nuvel eval`, `nuvel evalv2`.

Beside it, [nuvel-harness](https://github.com/Folken2/nuvel-harness) is the production machinery
for ADK agents: runs that survive a restart, routing and retry, context management, usage
metering and approval. It is a separate package with its own test suite. Generated ADK agents
do not use it yet. Today the template carries its own versions of some of the same pieces, and
moving them onto the harness is the first roadmap phase
([#168](https://github.com/Folken2/nuvel/issues/168)).

---

## Layers and ownership

| Layer | Owner | What lives there |
|---|---|---|
| **Model** | [LiteLLM](https://github.com/BerriAI/litellm) | gateway, virtual keys, budgets, spend attribution, provider translation, model-layer traces |
| **Execution** | an external cloud-workspace runtime | isolated workspaces (terminal, filesystem, browser) for background coding agents |
| **Agent runtime** | **nuvel-harness** | durable runs, retry and repair, context management, metering, approval, inside the agent's own service |
| **Authoring** | **nuvel** | scaffolding, skills, templates, overlays, agent-layer traces, eval |

nuvel builds the authoring layer and the agent runtime, and integrates with the other two rather
than reimplementing them. The model and execution layers are general-purpose infrastructure
maintained in the open. The agent runtime is where the behaviour every production agent needs
is written once and tested. The authoring layer is where one agent's process, gates and tools
are encoded in a form that can be reviewed and changed.

### Where the runtime meets the model layer

Both touch model calls, so the line is drawn by what each one knows:

- **The gateway** translates between providers, holds keys and budgets, and records what each
  call cost on the wire.
- **The harness** decides per turn which route to take (whose key, which fallback), retries with
  knowledge only the agent has (a context overflow, a tool call with broken arguments), and
  prices each call against the run, turn and tool that made it.

A gateway cannot tell that a retry was caused by an overflow. The harness does not translate
providers.

---

## Two trace layers: keep both, collapse neither

| Layer | Owner | Answers |
|---|---|---|
| **Model layer** | LiteLLM | Which provider, which key, how many tokens, what the call cost, did it hit a budget |
| **Agent layer** | nuvel (`nuvel traces`, `nuvel eval`) and the harness ledger | Which turn, which tool, did the skill load, did the gate fire, what the run cost, was the outcome correct |

The gateway cannot tell you that a gate did not fire: that is agent semantics. nuvel cannot tell
you which virtual key paid for a call: that is wire-level.

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
                    (on nuvel-harness)   (background coding)
```

- Generated ADK agents can ship a ready-to-run gateway with `nuvel agent create --with-litellm`,
  which routes the agent's LLM calls through a LiteLLM proxy instead of calling providers directly.
- The gateway is a **single point of failure for every runtime** behind it. It needs its own
  instance, a restart policy, and a documented bypass.
- It does **not** share a machine with anything it governs. A governance layer co-located with a
  governed component is not a governance layer.
- Its real sizing constraint is **spend-log storage**, not CPU: budget 10 to 20 KB per logged
  request when prompts are stored, and set retention accordingly.

---

## Non-goals

- **Cloud workspaces.** Sandboxed terminals, filesystems and browsers for background coding
  agents belong to the execution layer. Keeping an agent's own run alive across a restart is the
  harness's job, not a workspace's.
- **A provider abstraction.** LiteLLM already spans 100+ providers. The harness chooses routes
  and retries; it does not translate providers.
- **Wire-level telemetry.** Key attribution, budgets and per-provider spend belong at the gateway.
- **Breadth-first framework packaging.** A framework backend is supported when there is a
  concrete technical need for it, not for coverage's sake.
- **A second copy of the runtime.** Machinery every agent needs lives in nuvel-harness, not in
  each template.

---

## Design test for new work

Before starting anything, ask:

1. **Is this maintained in the open by another project?** If so, integrate with it.
2. **Is it agent-layer or model-layer?** Model-layer concerns go to the gateway.
3. **Does every production agent need it, whatever it does?** Then it belongs in nuvel-harness,
   tested once.
4. **Does it encode how one agent does its work** (skills, gates, tools, evaluation)? Then it is
   authoring-layer work. If none of these, it is probably not nuvel's.

> *Tested machinery, thick skills.* Every agent runs on the same harness. The leverage is in how
> the work is wired, not in the model weights.
