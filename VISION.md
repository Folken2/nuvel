# Nuvel Vision — November 2026

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

### Adopt Opik for the agent layer

We do not write our own tracing UI, dataset store or experiment runner either. **Opik** (Apache-2.0, self-hostable) is that layer: traces, datasets, experiments, scoring, dashboards.

Nuvel emits agent-layer traces into Opik — which turn, which tool, did the skill load, did the gate fire, what did it cost in agent terms — and defines what *correct* means as datasets and criteria. The third owner sharpens the split rather than blurring it:

| Layer | Owner | Answers |
|---|---|---|
| **Model** | LiteLLM | which provider, which key, tokens, cost, budget |
| **Runtime** | Opik | the trace tree, the dataset, the experiment, the score |
| **Semantics** | Nuvel | what *correct* means: the gates, the SOPs, the criteria |

Self-hosting is the point, not a preference. Agent traces carry a customer's process — their escalations, their exceptions, their data. A factory that ships its traces to a vendor's cloud is not a factory a regulated customer can buy from. Opik runs on our own box, next to nothing it governs.

---

## The software factory — our own first customer

We are not only shipping agents to customers. Nuvel runs its own shop on them.

The factory has three inputs we already own — **app templates**, **`nuvel agent create`**, and the **skills hub** — and one output: working software, in weeks. What it does not yet have is a *line*: intake, queue, station, gate, evidence. Today every deploy begins with a human asking and ends with a human deciding it is done. That is a workshop, not a factory.

A factory is defined by throughput, repeatability and a gate. So we build stations, in this order:

| # | Station | Trigger | The gate that proves it |
|---|---|---|---|
| 1 | **PR review** | `pull_request` webhook | the review is scored against the diff and the repo's tests |
| 2 | issue triage | `issues` webhook | labels and routing scored against a labelled set |
| 3 | release notes | tag / merge to main | every changelog line traces to a commit |
| 4 | QA and regression | deploy | the suite runs and reports, instead of "looked fine" |

Two rules hold the line together:

- **Every station leaves evidence.** A station whose output a human has to re-check has moved the work, not removed it.
- **Scale triggers, not boxes.** One orchestrator, webhook- and cron-driven, spawning ephemeral workers on capacity we already pay for. A station that is not worth a webhook is not worth a server.

### Station one: the PR review agent

Reviewed output is the cheapest agent output to verify — a diff and a test suite are ground truth — so it is the one station where we can score the agent *and* improve it. It also compounds: every engagement we take inherits a reviewer that already works.

**Harness: Hermes runs it, Nuvel becomes it, Pi tests it.**

- **Hermes runs station one.** It is deployed today with the identity and the credentials, it takes webhooks and cron natively, and it loads the review SOP as a skill already. Fastest path to a measured loop wins.
- **Nuvel becomes the harness once the loop is proven.** "Our PR reviewer is a Nuvel agent" is the strongest proof we can hand a customer — but dogfooding the scaffolder before the loop works is the wrong order.
- **Pi stays an experiment.** Its code-mode default — one `exec` tool, typed tool calls in a sandbox — is exactly what a review loop wants: fetch the diff, filter it, review, comment, in one script. Pi is where we measure whether code mode cuts the cost of a station.

The reviewer's value lives in the **skill and the eval set**, not the harness. Both stay harness-agnostic, so a station can move runners without being rewritten.

---

## What we stop building

- **Our own agent runtime.** Cloud workspaces, harness abstraction, checkpointing, session resume.
- **Our own model routing / provider abstraction.** LiteLLM already spans 100+ providers.
- **Our own model-layer telemetry.** Cost, tokens, latency and key attribution belong at the gateway.
- **Our own tracing UI, dataset store or experiment runner.** Opik is open source and self-hosted; what we keep is the criteria, not the dashboard.
- **Breadth-first harness packaging.** If a harness is not a customer requirement, it is not our surface.

## What we keep and double down on

- **The scaffolder** — `nuvel agent create`, per-framework templates and overlays.
- **The skills hub** — the moat. Versioned, composable, portable executable markdown.
- **The meta-agent** — `nuvel run`, the thing that writes agents.
- **The agent-layer trace and eval stack** — `nuvel traces`, `nuvel eval`, `evalv2`, now emitting into Opik.
- **The factory line** — the stations above, running on our own code before a customer sees them.
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

        ┌──────────────────────────────────────┐
        │              Opik                    │
        │  agent traces · datasets · evals     │
        └──────────────────────────────────────┘
                 ▲ agent-layer traces only
```

- The gateway is a **single point of failure for every runtime** — that is the cost of centralised governance, and it is worth paying once. It needs its own instance, a restart policy, and a documented bypass.
- It does **not** share a machine with anything it governs. A governance layer co-located with a governed component is not a governance layer.
- Its real sizing constraint is **spend-log storage**, not CPU: budget 10–20 KB per logged request when prompts are stored, and set retention accordingly.
- Opik carries customer process, so it is **self-hosted on our hardware** and receives agent-layer traces only. It is never on the inference path: if Opik is down, agents still run.

---

## The test for every future proposal

Before starting anything, ask:

1. **Is this commodity?** If a well-funded team maintains it in the open, adopt theirs.
2. **Does it know our customer's process?** If not, it is not our differentiator.
3. **Is it agent-layer or model-layer?** Model-layer goes to the gateway.
4. **Would a customer pay for this specifically?** If the honest answer is no, it is infrastructure we should not be writing.

> *Thin harness, thick skills.* The leverage is not in the model weights — it is in how you wire the work.
