---
name: adk-memory-self-improvement
description: EXPERIMENTAL, opt-in memory self-improvement for long-lived ADK agents — relevance-conditioned preload, the periodic consolidation "dream" pass, the after-turn judge fork, fire-and-forget sibling runs, per-session throttling, and the skill curator that proposes SKILL.md files. Read before enabling any NUVEL_MEMORY_* or NUVEL_SKILL* flag, when budgeting the extra LLM calls these add per turn, or when an agent should improve its own memory and skills over months. Every fork-adding feature here defaults off (preload is the on-by-default exception) and none should reach production without eval coverage.
---

# Memory self-improvement for ADK agents

## Status: experimental

Everything in this skill is **opt-in and, except for preload, defaults off**. "Experimental" here means something specific: each mechanism — the dedupe/reconcile math, the throttle, the whitelist, the drain — is unit-tested in isolation and behaves the way its tests say it does. What is *not* proven is the thing that actually matters: whether letting an agent rewrite its own memory and propose its own skills improves answer quality over a long-running session, or slowly degrades it through compounding small mistakes (a bad consolidation merge, a judge that saves the wrong fact, a curator proposal nobody reviews). There is no eval suite yet that scores this layer's effect over weeks or months of real usage.

Treat this skill as an honest map of the mechanism and its cost, not a recommendation to turn it on. **Do not enable any of these flags in production without eval coverage** — build a harness with `nuvel eval` that measures answer quality with the flag on vs. off before you ship it, and re-run that eval after any prompt or threshold change.

## Relevance-conditioned preload

`NUVEL_MEMORY_PRELOAD` (`memory/preload.py:30`, default `"1"` — **on**) replaces whole-file memory injection with retrieval: instead of pasting all of `AGENT_MEMORY.md` plus every topic file into the prompt each turn, `retrieve_memory_block()` surfaces only the chunks relevant to the current query. `NUVEL_MEMORY_PRELOAD_TOP_K` (`preload.py:31`, `DEFAULT_TOP_K = 5`) caps how many chunks get injected.

Two backends, tried in order: if an ADK `BaseMemoryService` (e.g. `OrgMemoryService`) is attached to the invocation context, its `search_memory()` does the ranking. Otherwise `rank_markdown_chunks()` — a pure, dependency-free function — ranks the markdown store's chunks by lexical token overlap with the current query and returns the top-K.

This is the odd one out in this skill: it is **on by default**, and it **reduces** tokens per turn rather than adding LLM calls. Setting `NUVEL_MEMORY_PRELOAD=0` reverts to the legacy whole-file injection (`state/memory.load_all_memory()`), which is safe but pastes everything regardless of relevance. Don't lump this in with the cost-adding forks below when budgeting spend — it's the one line item that helps your bill, not hurts it.

## The consolidation "dream" pass

`NUVEL_MEMORY_CONSOLIDATION` (`memory/consolidation.py:35`, default **off**) gates a periodic, store-agnostic job that reviews a user's accumulated raw memories: exact-text dedupe, then optional cosine-similarity dedupe of near-duplicates, then deterministic contradiction reconciliation (newest statement about the same subject+verb wins), then — if an LLM is injected — a distilled structured profile (`profile.py`: `summary`, `role`, `interests`, `durable_facts`).

`NUVEL_MEMORY_CONSOLIDATION_INTERVAL` (`consolidation.py:36`, `DEFAULT_INTERVAL_SECONDS = 24 * 3600` — daily) paces the scheduler. `NUVEL_MEMORY_CONSOLIDATION_SIM` (`consolidation.py:37`, `DEFAULT_SIM_THRESHOLD = 0.92`) is the cosine-similarity floor above which two entries are treated as near-duplicates and collapsed to the longer (more specific) one.

**The failure mode of setting the threshold too low:** cosine similarity is a blunt instrument. Drop `NUVEL_MEMORY_CONSOLIDATION_SIM` toward, say, `0.7`, and two genuinely distinct facts that merely share vocabulary ("user prefers dark mode" / "user prefers minimal UI") can register as near-duplicates and get merged — the shorter, more specific one is discarded and gone. This loss is **irreversible**: the dedupe step doesn't keep the dropped original anywhere. Move the threshold down in small increments, if at all, and only after checking what actually gets collapsed at the new value.

The dedupe/reconcile core (`dedupe_exact`, `dedupe_similar`, `reconcile_contradictions`, `cosine_similarity`) is pure — no I/O, LLM, or embedder required — so it unit-tests deterministically; only `consolidate_memories()`'s profile-generation step needs an injected `llm_fn`/`embed_fn`.

## The after-turn judge fork

`NUVEL_MEMORY_REVIEW_FORK` (`memory/review_fork.py:39`, default **off** — gate checked against `"0"`) runs `review_fork_callback`, an ADK `after_agent_callback`. Once the parent agent's turn is done, it hands a throwaway "judge" `LlmAgent` (built on `FAST_MODEL`) the just-finished conversation and asks: what, if anything, is worth saving? The judge's toolset is whitelisted to `save_memory` / `update_memory` and the skill-review tools (`list_skills`, `read_skill`, `propose_skill`) via `fork_utils.make_whitelist_callback` — anything else it tries to call is refused with an error result, not a crash.

It is a *fork*: `review_fork_callback` hands the judge to `SIBLING_RUNNER.spawn(...)` and returns immediately. The parent's reply has already gone out to the user before the judge even starts running. The judge `LlmAgent` deliberately has **no** `after_agent_callback` of its own — a structural guarantee that a judge can never spawn a judge of a judge.

## Sibling runs and draining

`sibling_runner.py`'s `SiblingRunner` (registered process-wide as `SIBLING_RUNNER`) owns the mechanics every fork needs: a throwaway `InMemorySessionService`, a throwaway `Runner`, `asyncio.create_task` bookkeeping, and an exception-swallowed drive loop (`_run_one` catches and logs everything). It is itself an ADK `BasePlugin`, registered purely for its `close()` hook — with nothing spawned it does nothing, so it is always safe to leave in the plugin chain regardless of whether any fork is enabled.

At shutdown, `close()` awaits every in-flight sibling task up to `NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT` (`sibling_runner.py:34`, `DEFAULT_DRAIN_TIMEOUT = 4.0` seconds), capped at `MAX_DRAIN_TIMEOUT = 4.5` seconds no matter what you set it to — that cap keeps the drain strictly under ADK's 5-second plugin-close budget, so ADK never cancels it mid-drain and turns a clean shutdown into a user-visible error.

Both directions of this knob have a real cost:

- **Too short a drain** silently discards work. A sibling task still writing a memory when the timeout fires is logged as "unfinished" and dropped — no retry, no replay.
- **Too long a drain** delays every deploy, since the process can't exit until the drain either finishes or times out.

The 4.5s ceiling exists precisely so you can't accidentally trade the second problem for a much worse one (ADK force-cancelling the close and surfacing an error).

## Throttling is the safety valve

`throttle.try_claim(state, fork_type)` (`memory/throttle.py:54`) is not an optional extra alongside the forks above — treat it as a required companion to enabling any of them. Two independent limits, both keyed per fork type in session state:

- **Cooldown** — `NUVEL_MEMORY_FORK_COOLDOWN` (`throttle.py:27`, `DEFAULT_COOLDOWN_SECONDS = 120.0`). A second fork of the same type can't launch until this many seconds have passed since the last one claimed a slot. Set to `0` to disable the cooldown (the cap still applies) — useful for tests that want every-turn behavior, not for production.
- **Per-session cap** — `NUVEL_MEMORY_FORK_CAP` (`throttle.py:28`, `DEFAULT_PER_SESSION_CAP = 50`). A hard ceiling on how many times a fork type can claim a slot in one session, independent of timing — the backstop against a session that runs long enough to blow through cooldown gates one at a time.

`try_claim` checks the cap first, then the cooldown; either check failing returns `False` and the caller treats that as "skip this turn" — no error, no retry, the turn just proceeds without spawning a fork. `state=None` (no session state available) always claims, since there's nothing to throttle against.

Without a cooldown, a burst of quick user turns can launch a fork on every single one, each one reviewing a near-identical conversation snapshot to the last. The cap exists because a long enough session can still exhaust a cooldown-respecting schedule; treat both together as the one thing standing between "an occasional useful review" and "a background LLM-call generator."

## The skill curator

`NUVEL_SKILL_CURATOR` (`plugins/skill_curator_plugin.py:51,74`, default **off** — the gate is `os.environ.get(ENV_ENABLED, "").strip() in {"1","true","yes","on"}`, so an unset value is off) lets `SkillCuratorPlugin` propose new or patched `SKILL.md` files from what it observed during a run: tool-call volume, event count, and repeated tool errors on the same tool.

It runs at *Runner scope*, not per-turn — `before_run_callback` resets counters, `after_tool_callback`/`on_event_callback`/`on_tool_error_callback` accumulate signal across the whole run (including sub-agents), and `after_run_callback` evaluates once at the end. A run only reaches the LLM call if it's "complex enough" by any of three thresholds:

| Env var | Default | Constant | Trips when |
|---|---|---|---|
| `NUVEL_SKILL_CURATOR_MIN_TOOLS` | `5` | `DEFAULT_MIN_TOOLS` | tool calls in the run ≥ 5 |
| `NUVEL_SKILL_CURATOR_MIN_EVENTS` | `12` | `DEFAULT_MIN_EVENTS` | ADK events in the run ≥ 12 |
| `NUVEL_SKILL_CURATOR_MIN_ERRORS` | `3` | `DEFAULT_MIN_ERRORS` | the same tool errors ≥ 3 times |

`NUVEL_SKILL_CURATOR_MODEL` (`DEFAULT_MODEL = "gemini-2.0-flash"`) is the model used for the curation call itself, via `google.genai` (already an ADK dependency — no new third-party deps). `NUVEL_SKILL_PROPOSALS_DIR` overrides where proposals are written (default `~/.nuvel/skill-proposals`, deliberately outside the project tree); `NUVEL_SKILLS_DIR` overrides where the curator looks for the existing skill catalog to avoid re-proposing what already exists (default: the generated agent's own `<package>/skills/`).

`skill_curator` and `sibling_runner` both ship **installed** in every generated agent's plugin chain (`plugins/__init__.py.tmpl`'s `PLUGIN_INSTANCES`) — you don't need to add either yourself. They are **inert unless their own env var is set**: `skill_curator` no-ops in `after_run_callback` when `NUVEL_SKILL_CURATOR` is unset, and `sibling_runner` (`SIBLING_RUNNER`) does nothing at all unless some fork actually calls `.spawn()`.

Proposals from the curator are strictly *proposals*: `_write_proposal` writes a markdown file with `action`, `skill_name`, `triggering_agents`, a rationale, and a patch/body — it never touches the live skill catalog. The review-fork's own `propose_skill` tool (`memory/skill_review.py`) writes into the same directory, so the two loops (Runner-scope heuristics vs. after-turn conversation review) complement each other in one human-review queue. A human must read and apply a proposal before it becomes a live skill — see `adk-skill-creation` for what a well-formed `SKILL.md` needs to look like once you do.

## Cost model

This is the section to read before flipping anything on. Every enabled fork adds LLM calls **per turn** — forks are fire-and-forget, so their *latency* is off the critical path (the user's reply already went out), but their *spend* is not. A fire-and-forget call still bills the same as any other call.

Concretely: with the judge fork on (`NUVEL_MEMORY_REVIEW_FORK=1`) and no effective throttle (cooldown at `0`, cap high), the worst case is **one extra model call per turn, per enabled fork type**. Today that's one fork type (the review/judge fork); the skill curator adds a second, less frequent source — its LLM call only fires when a run crosses one of the three complexity thresholds above, not every turn. A 100-turn session with the judge fork unthrottled can add up to 100 extra model calls on top of the 100 the parent agent already makes — doubling the session's total call count. Add the consolidation pass's own LLM call (once per `NUVEL_MEMORY_CONSOLIDATION_INTERVAL`, default daily) and any curator-triggered proposal calls, and the multiplier compounds further with every additional flag you turn on.

Throttling (`NUVEL_MEMORY_FORK_CAP`, `NUVEL_MEMORY_FORK_COOLDOWN`) is what keeps this bounded instead of linear-in-turns — see "Throttling is the safety valve" above. Preload is the exception that *reduces* spend and is safe to leave on regardless.

To measure what any of this is actually costing you: `CostGuardPlugin` (wired in every generated agent's plugin chain) tracks per-turn spend against `nuvel/plugins/pricing.json`; `nuvel traces` inspects the per-conversation `.json`/`.jsonl` trace files that record every model call, including fork-spawned ones; `nuvel pricing` reports the rates those traces are priced against. The operating rule: **enable one flag at a time, run it under real traffic, and measure with these tools before adding the next one.** Enabling the judge fork, consolidation, and the curator simultaneously and only then checking the bill is how a surprise invoice happens.

## When NOT to use

- **Cost-sensitive or high-QPS deployments.** Every enabled fork is a per-turn LLM call multiplier (see Cost model). At volume, that multiplier is the dominant cost driver, not the parent agent's own calls.
- **Stateless task bots.** If the agent doesn't carry state between sessions — a one-shot classifier, a webhook handler — there is nothing for consolidation, the judge fork, or the curator to act on. Preload alone (already on) is enough; the rest is overhead with no target.
- **Anything requiring deterministic behaviour across deploys.** This carries the same caveat `--persona` agents carry: a support bot that rewrites its own memory mid-conversation, or reconciles a "contradiction" the wrong way, is a regression in behavior, not a feature. If two runs of the same conversation must produce the same answer, none of these self-modifying paths belong in that agent.
- **Any deployment without eval coverage.** Repeated from Status: don't turn any of these on in production until you have a `nuvel eval` harness that can tell you, with the flag on vs. off, whether answer quality actually improved or quietly regressed.

## Quick reference

| Env var | Default | Source |
|---|---|---|
| `NUVEL_MEMORY_PRELOAD` | `1` (on) | `preload.py:30,38` |
| `NUVEL_MEMORY_PRELOAD_TOP_K` | `5` | `preload.py:31-32` (`DEFAULT_TOP_K`) |
| `NUVEL_MEMORY_CONSOLIDATION` | off | `consolidation.py:35,50-51` |
| `NUVEL_MEMORY_CONSOLIDATION_INTERVAL` | `86400` (daily) | `consolidation.py:36,39` (`DEFAULT_INTERVAL_SECONDS`) |
| `NUVEL_MEMORY_CONSOLIDATION_SIM` | `0.92` | `consolidation.py:37,40` (`DEFAULT_SIM_THRESHOLD`) |
| `NUVEL_MEMORY_REVIEW_FORK` | off | `review_fork.py:39,95` |
| `NUVEL_MEMORY_FORK_COOLDOWN` | `120` sec | `throttle.py:27,30` (`DEFAULT_COOLDOWN_SECONDS`) |
| `NUVEL_MEMORY_FORK_CAP` | `50` | `throttle.py:28,31` (`DEFAULT_PER_SESSION_CAP`) |
| `NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT` | `4.0` sec (cap `4.5`) | `sibling_runner.py:34-36` |
| `NUVEL_SKILL_CURATOR` | off | `skill_curator_plugin.py:51,74` |
| `NUVEL_SKILL_CURATOR_MIN_TOOLS` | `5` | `skill_curator_plugin.py:52,59` |
| `NUVEL_SKILL_CURATOR_MIN_EVENTS` | `12` | `skill_curator_plugin.py:53,60` |
| `NUVEL_SKILL_CURATOR_MIN_ERRORS` | `3` | `skill_curator_plugin.py:54,61` |
| `NUVEL_SKILL_CURATOR_MODEL` | `gemini-2.0-flash` | `skill_curator_plugin.py:57,62` |
| `NUVEL_SKILL_PROPOSALS_DIR` | `~/.nuvel/skill-proposals` | `skill_curator_plugin.py:55,100-103` |
| `NUVEL_SKILLS_DIR` | `<package>/skills/` | `skill_curator_plugin.py:56,236-239` |

Deeper dive: `references/fork-lifecycle.md` (trigger point, throttle check order, what a fork sees, write-back, drain-at-shutdown, failure handling). Related skills: `adk-org-memory-retrieval` (the optional embedding-ranked backend preload retrieves through), `adk-skill-creation` (what a curator/judge proposal needs to become a real skill), `adk-prompt-engineering` (the stable/session/volatile prompt tiers a fork must not mutate).
