---
name: adk-long-horizon-sessions
description: >-
  Surviving long ADK runs — resumability so an interrupted invocation
  continues instead of restarting, and event compaction so a long session
  doesn't exhaust the context window. Read when an agent runs for hours,
  when a session's history is growing past the context window, when
  deciding whether RESUMABILITY should stay on for a stateless deployment,
  or when tuning COMPACTION_INTERVAL / COMPACTION_OVERLAP /
  COMPACTION_RETENTION. Pairs with adk-long-horizon-guardrails (which stops
  runaway runs) and adk-prompt-engineering (the cache-stable prompt tiers,
  a separate but adjacent long-horizon cost lever).
---

# Long-horizon sessions for ADK agents

## Stopping versus surviving

`adk-long-horizon-guardrails` stops a run that has gone *wrong* — a response loop, a tool retrying the same failing call forever. This skill is the other half: keeping a run that is going *right* alive across interruptions and a session history that keeps growing. Guardrails halt; this skill resumes and compacts. Both matter for an agent that runs for hours or unattended, but they answer different questions.

## Resumability

`AgentHarness.resumability_config` (`harness.py.tmpl:204-211`) returns `ResumabilityConfig(is_resumable=enabled)` from `google.adk.apps._configs`, and the `App` built in `app_for` (`harness.py.tmpl:235`) wires it in as `resumability_config=self.resumability_config`.

```python
enabled = os.getenv("RESUMABILITY", "true").lower() in ("true", "1", "yes")
return ResumabilityConfig(is_resumable=enabled)
```

`RESUMABILITY` **defaults to `true`**. With it on, an invocation interrupted mid-run — a crashed process, a redeploy, a dropped connection — resumes from where it left off instead of restarting from turn one. For a long-horizon agent this is the difference between losing a few seconds of work and losing an hour of it.

The trade-off is explicit, not free: a resumable run persists more invocation state per turn so there's something to resume *from*. That storage cost is what buys you not losing progress to a restart. Set `RESUMABILITY=false` when a deployment genuinely can't carry that extra state — a stateless demo with no durable session backing, for instance. Turn it off deliberately, as a decision about your infrastructure, rather than discovering later that persistence is silently failing because the store underneath isn't there.

## Event compaction

`AgentHarness.compaction_config` (`harness.py.tmpl:213-225`) returns an `EventsCompactionConfig` (same `google.adk.apps._configs` module), wired into the `App` as `events_compaction_config=self.compaction_config` (`harness.py.tmpl:236`). It rolls old events into summaries so a session that runs for a long time doesn't eventually push its raw history past the model's context window.

```python
return EventsCompactionConfig(
    compaction_interval=int(os.getenv("COMPACTION_INTERVAL", "8")),
    overlap_size=int(os.getenv("COMPACTION_OVERLAP", "2")),
    event_retention_size=int(os.getenv("COMPACTION_RETENTION", "20")),
)
```

| Env var | Default | Meaning |
|---|---|---|
| `COMPACTION_INTERVAL` | `8` | Sliding window of user turns that triggers a compaction pass. |
| `COMPACTION_OVERLAP` | `2` | Events shared between one summary and the next. |
| `COMPACTION_RETENTION` | `20` | Most recent events kept verbatim, never summarized. |

**Why overlap matters.** If consecutive summaries covered disjoint spans of history, the boundary between them would be a clean cut with no shared context — and a clean cut loses the causal thread. A decision made near the end of window N ("switch to plan B because X failed") can end up summarized away from the reasoning that produced it, so a turn in window N+1 sees the *outcome* but not the *why*. A small overlap means each new summary is written with some of the same raw events the previous summary saw, so the causal link survives the boundary instead of being severed by it.

## Compaction versus the ContextWindow plugin

These are adjacent but distinct mechanisms, and both ship active by default — don't conflate them:

- **Event compaction** (`compaction_config`, above) *rewrites* history. It's a property of the `App`/`Runner`, consulted by the session service, and it changes what's actually stored and replayed as session state.
- **`ContextWindowPlugin`** (`plugins/context_window_plugin.py`) only *observes*. It's read-only: after every model call it computes a `context_window` usage snapshot (tokens used, percent of the model's window) and writes it to session state for a frontend to render, and — if `CONTEXT_WINDOW_WARN_PCT` is set above `0` (default `0`, disabled) — logs a one-time warning when usage crosses that percent. It never mutates the request and never blocks a call. See `CONTEXT_WINDOW_CONFIG` / `CONTEXT_WINDOW_DEFAULT` / `CONTEXT_WINDOW_WARN_PCT` in `.env.example`.
- `CONTEXT_FILTER_KEEP` (default `10`, `ContextFilterPlugin`) is a third, related knob: it caps how many prior *invocations* are kept in the outgoing prompt, independent of both compaction and the window monitor.

In short: compaction changes what history exists; `ContextWindowPlugin` tells you how much of the window that history (plus everything else) is using; `CONTEXT_FILTER_KEEP` trims how many invocations are replayed into the prompt at all. They're complementary controls on the same underlying pressure, not the same mechanism.

## Why this sits next to the prompt-tier contract

Compaction and the three-tier system prompt (`prompt/instructions.py.tmpl`) are **structurally separate** — don't go looking for a causal link between them. Compaction operates on the ADK Session's event list; `build_session_tier(ctx)` builds Tier 2 from exactly two things, a user-profile block and relevance-retrieved memory, and never reads or reassembles conversation/event history. A `grep -niE 'compact|event|history'` over `instructions.py.tmpl` turns up nothing, because there is nothing there to turn up: compacting the event log does not rewrite, move, or otherwise touch any tier builder.

The reason these topics still belong in one skill is thematic, not mechanistic: both are cache- and cost-sensitive concerns on a long-running session. `build_stable_tier()` is rebuilt fresh from persona/soul files every turn and is unaffected by anything compaction does — which is exactly what you want, because the **stable tier must stay byte-identical turn over turn** for the provider's cached prefix to stay hot, and a cache hit costs a fraction of a fresh input token. Compaction earns you headroom on the *event* side of the ledger; a byte-identical stable prefix earns you a cheap prompt on the *instruction* side. They're two independent levers on the same turn's total cost, not one mechanism feeding the other.

`tests/test_prompt_tiers.py` pins the stable-prefix contract on its own terms (unaffected by compaction either way). The full three-tier contract — stable / session / volatile, what belongs in each, and how `InstructionProvider` assembles them — lives in `adk-prompt-engineering`; read that skill for the complete tier design.

## When NOT to use

- **Short request/response agents.** If a session is a handful of turns and ends, resumability buys you nothing (there's rarely an interruption worth surviving) and compaction never fires — `COMPACTION_INTERVAL` turns never accumulate. The defaults are harmless to leave on, but don't spend tuning effort here.
- **Stateless infrastructure that can't persist invocation state.** If your deployment has no durable session store, `RESUMABILITY=true` doesn't fail loudly — it just has nothing to resume from. Turn it off deliberately (`RESUMABILITY=false`) rather than finding out the hard way after an interruption.
- **Sessions short enough that summarizing would cost you context you still need.** Compaction trades detail for headroom. If a session's full raw history comfortably fits the model's window for its whole lifetime, a low `COMPACTION_INTERVAL` just summarizes away detail you didn't need to lose yet — raise the interval, or leave the default, rather than compacting eagerly.

## Quick reference

| Env var | Default | Property |
|---|---|---|
| `RESUMABILITY` | `true` | `AgentHarness.resumability_config` → `ResumabilityConfig` |
| `COMPACTION_INTERVAL` | `8` | `AgentHarness.compaction_config` → `EventsCompactionConfig` |
| `COMPACTION_OVERLAP` | `2` | `AgentHarness.compaction_config` → `EventsCompactionConfig` |
| `COMPACTION_RETENTION` | `20` | `AgentHarness.compaction_config` → `EventsCompactionConfig` |

Related: `adk-long-horizon-guardrails` for stopping a run that's gone wrong; `adk-prompt-engineering` for the full three-tier prompt contract this skill's cache-stability rule depends on.
