# Nuvel Vision — October 2026

> **Adopt the commodity. Build the differentiator.**
>
> Infrastructure is being solved by teams with more resources than us. Agent *authoring* is not.

---

## The refocus

We were drifting toward building everything: our own runtime, our own routing, our own model-layer telemetry. Each of those is a full-time problem, and each already has a well-funded team on it.

The correction is to be deliberate about which layer we own.

| Layer | Who owns it | What lives there |
|---|---|---|
| **Model** | **LiteLLM** | gateway, virtual keys, budgets, spend attribution, provider routing, guardrails, model-layer traces |
| **Execution** | **Moyai** (or equivalent) | isolated cloud workspaces — terminal, filesystem, browser — for background coding agents |
| **Authoring** | **Nuvel** | production-grade agents for customers: scaffolding, skills, templates, overlays, eval, compliance |

We build the third. We adopt the first two.

---

## Why this is a strengthening, not a retreat

The temptation is to read "adopt instead of build" as giving something up. It isn't. It sharpens the product:

- The gateway is commodity. Every company routing LLM traffic needs it, none of them should write it.
- The cloud-workspace runtime is commodity. Six harnesses, isolated machines, checkpointing — solved, open source, and better-funded than we are.
- **What neither of them does is know your customer.** Neither knows your gates, your SOPs, your service areas, your escalation matrix, your audit requirements. That is authoring, and it is what a customer actually pays for.

A customer does not buy a gateway. They buy an agent that does their job correctly, with their process encoded, in a form they can review and change.

---

## Two trace layers — keep both, collapse neither

This distinction matters because it is the reason no existing work is wasted.

| Layer | Owner | Answers |
|---|---|---|
| **Model layer** | LiteLLM | Which provider, which key, how many tokens, what did it cost, who spent it, did it hit a budget |
| **Agent layer** | Nuvel (`nuvel traces`, `nuvel eval`) | What turn, which tool, did the skill load, did the gate fire, was the outcome correct |

LiteLLM cannot tell you that a gate did not fire — that is agent semantics, and it is ours. Nuvel cannot tell you which virtual key paid for the call — that is wire-level, and it is LiteLLM's.

**LiteLLM governs the wire. Nuvel governs the agent.**

---

## What we stop building

- **Our own agent runtime.** Cloud workspaces, harness abstraction, checkpointing, session resume.
- **Our own model routing / provider abstraction.** LiteLLM already spans 100+ providers.
- **Our own model-layer telemetry.** Cost, tokens, latency and key attribution belong at the gateway.
- **Breadth-first harness packaging.** If a harness is not a customer requirement, it is not our surface.

## What we keep and double down on

- **The scaffolder** — `nuvel agent create`, per-framework templates and overlays.
- **The skills hub** — the moat. Versioned, composable, portable executable markdown.
- **The meta-agent** — `nuvel run`, the thing that writes agents.
- **The agent-layer trace and eval stack** — `nuvel traces`, `nuvel eval`, `evalv2`.
- **Production grade for customers** — the part infrastructure vendors structurally cannot serve.

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
     Hermes          Nuvel agents            Moyai
   (PoC/orchestr.)  (customer-facing)    (background coding)
```

- The gateway is a **single point of failure for every runtime** — that is the cost of centralised governance, and it is worth paying once. It needs its own instance, a restart policy, and a documented bypass.
- It does **not** share a machine with anything it governs. A governance layer co-located with a governed component is not a governance layer.
- Its real sizing constraint is **spend-log storage**, not CPU: budget 10–20 KB per logged request when prompts are stored, and set retention accordingly.

---

## The test for every future proposal

Before starting anything, ask:

1. **Is this commodity?** If a well-funded team maintains it in the open, adopt theirs.
2. **Does it know our customer's process?** If not, it is not our differentiator.
3. **Is it agent-layer or model-layer?** Model-layer goes to the gateway.
4. **Would a customer pay for this specifically?** If the honest answer is no, it is infrastructure we should not be writing.

> *Thin harness, thick skills.* The leverage is not in the model weights — it is in how you wire the work.
