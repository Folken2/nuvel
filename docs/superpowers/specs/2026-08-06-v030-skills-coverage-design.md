# v0.3.0 knowledge-layer coverage — design

**Date:** 2026-08-06
**Status:** approved
**Author:** Folken2

## Problem

Six feature PRs landed on `main` between `v0.2.0` (`1cb1fc3`) and `643994f` —
13,044 insertions across 102 files. Two of them (#48, #49, the `--with-acp` work)
updated the knowledge layer in the same commit, following the repo's convention.
The other four did not:

| PR | Feature | Docs shipped |
|---|---|---|
| #50 | Long-horizon guardrails, resumability, memory self-improvement | none |
| #51 | Cron isolation — scoped secrets, headless policy, HITL gate | none |
| #54 | OrgMemoryService runner wiring | internal `docs/memory/` + plans only |
| #55 | Hybrid RRF + knowledge graph + relational recall | none |

The result is a product regression, not a docs backlog. nuvel's deliverable *is*
its knowledge layer: skills are one of the three things the repo ships. Code
landed; the knowledge to operate it did not.

Concretely:

- **Six subsystems have zero skill coverage**: halt/command guardrails, cron
  isolation, org-memory retrieval, memory self-improvement, the skill curator
  (undocumented enough that it was found only by reading PR #50's plugin wiring),
  and long-horizon session survival — resumability, event compaction, and the
  cache-stable 3-tier system prompt.
- **12 environment variables read by template code are absent from `.env.example`**,
  five of them new in PR #50: `RESUMABILITY`, `COMPACTION_INTERVAL`,
  `COMPACTION_OVERLAP`, `COMPACTION_RETENTION`, `EXFIL_GUARD_STRICT`. The older
  seven are `CACHE_MAX_SIZE`, `CACHE_TTL_SECONDS`, `LLM_NUM_RETRIES`,
  `LLM_REQUEST_TIMEOUT`, `NO_COLOR`, `FORCE_COLOR`, `RECORD`.
- `CLAUDE.md:38` states skill counts of `8 / 6 / 5`; actual is `10 / 6 / 5`.
- `adk-composio-tool-router/SKILL.md:106` promises `references/composio-patterns.md`,
  which does not exist — the only dangling reference across all 10 ADK skills.
- `nuvel/memory/` contains four modules describing themselves as reimplementations
  of `garrytan/gbrain` with no attribution file in the repo.

## Goals

1. Every subsystem shipped in the six PRs has skill coverage a reader can operate from.
2. Every environment variable the template reads is present in `.env.example`, which
   remains the single canonical home — not duplicated elsewhere.
3. The drift that caused this is prevented mechanically, not by discipline.
4. gbrain attribution is discharged.
5. `v0.3.0` ships with an accurate, coherent knowledge layer.

## Non-goals

- Changing any subsystem's behaviour. This is a documentation and test change;
  the only production code touched is whatever the integrity test forces.
- Auditing the `claude_agent_sdk` (6) or `anthropic_managed_agents` (5) skills.
  Every new feature is ADK-only, so expected findings there are ~zero.
- Modifying `.github/workflows/release.yml`. A broken release pipeline is a bad
  thing to discover at tag time. Switching `--generate-notes` to `--notes-file`
  is recorded as a future option only.
- Scrubbing gbrain references. The lineage is an asset and the licence permits it.

## Context that shaped the design

**Bundled skills are authoring-time knowledge only.** `nuvel/backends/adk/scaffold.py`
never copies `nuvel/backends/adk/skills/` into generated agents; the generated
`<pkg>/skills/` directory ships empty (`.gitkeep`) for the agent's *own* domain
skills. The 10 bundled skills reach readers exclusively via `nuvel skills list|search`
and as drop-in skills for coding agents. **New skills therefore cost generated agents
zero runtime context**, which removes the usual pressure to be terse.

**gbrain is MIT-licensed and compatible.** `garrytan/gbrain` (27.8k stars,
TypeScript, MIT, © 2026 Garry Tan) is the origin of the retrieval algorithm design.
All four source files cited in nuvel's docstrings (`hybrid.ts`, `relational-intent.ts`,
`relational-recall.ts`, `link-extraction.ts`) exist in that repo, so the docstrings
are truthful. MIT → MIT is fully compatible; the obligation attaches to copies or
substantial portions, and nuvel is a cross-language reimplementation. A
`THIRD_PARTY.md` discharges it cleanly. RRF itself is published prior art
(Cormack, Clarke & Büttcher, 2009), independent of gbrain.

**`.env.example` is already the canonical env reference, and it is in good shape.**
222 lines documenting 61 variables, and the four undocumented PRs *did* add their
`NUVEL_CRON_*` / `NUVEL_MEMORY_*` / `NUVEL_ORG_*` entries to it. An earlier reading of
this gap — "~31 vars, zero documented" — came from grepping `README.md` alone and was
misleading. The correct conclusion is the opposite of adding an env table to the
README: **duplicating 61+ variables into a second document would create exactly the
second drift surface this spec exists to eliminate.** `.env.example` stays canonical,
gains the 12 missing entries, and the README points at it.

**Maturity is not uniform.** `GuardrailsPlugin` and `CronIsolationPlugin` are wired
unconditionally into every generated agent's plugin chain (though the latter is inert
outside cron runs). Every memory feature is opt-in behind an env var and defaults off.
The memory self-improvement layer is **experimental** and must be documented as such.

## Design

### Five new ADK skills

Split on audience and maturity, not on how the code happened to land. Each follows
the house style established by `adk-composio-tool-router`: frontmatter `description`
written as trigger conditions, an intro that says what the thing *is*, a "what ships"
section with real code, an env-var table, operational guidance, a **"When NOT to use"**
section, and a quick reference.

#### 1. `adk-long-horizon-guardrails`

~180 lines + `references/halt-latch-internals.md`.

Covers the two families in `nuvel/guardrails/`:

- **Halt guards.** The halt latch is the shared primitive and leads the skill:
  `latch_halt(reason)` latches only if no halt is already set;
  `halt_consumer_callback` runs as a `before_model_callback` and short-circuits the
  model with the canonical `[halted: <reason>]` envelope while latched;
  `acknowledge_halt` clears it; `reset_halt_handoff` clears the once-per-halt
  handoff flag at a user-turn boundary. Then the two detectors: `NoProgressGuard`
  (`after_model_callback`; byte-identical model text `window` times running) and
  `RepeatedFailureGuard` (`after_tool_callback`; fingerprint is `tool_name` plus a
  SHA-256 of canonical arguments, `threshold` consecutive failures latches, a
  success clears that signature's streak). `GuardrailsPlugin` binds all three to a
  single turn boundary.
- **Command / exfiltration guards.** The transferable insight is that
  `command_safety` classifies **structurally at the argv level** — `shlex` lexing
  per segment, unwrapping a single `bash -c '<inner>'` and recursing, returning the
  strongest verdict across segments (`deny` / `ask` / `allow`) — rather than
  substring-matching the raw string, which quoting and spacing defeat. Then
  `exfil_guard` as a `before_tool_callback` scanning tool arguments for
  high-confidence secret patterns, with strict (block) vs lax (flag) behaviour.

"When NOT to use": short interactive agents where a latched halt is user-visible
friction; tuning thresholds for agents whose legitimate behaviour is repetitive.

The reference file documents state keys, callback ordering, and how to author a
custom guard that latches correctly.

Cross-references `adk-callbacks-hitl` (these are callbacks) and `adk-agent-patterns`
(loop patterns need a runaway backstop).

#### 2. `adk-cron-isolation`

~160 lines.

Leads with the premise: a scheduled run has no human present to approve tool calls,
so it needs a bounded blast radius. Then the mechanism — three async-local
`ContextVar` markers installed together by `cron_isolation()` around a job's
invocation and reset on exit:

1. the cron-run marker (which `job_id`),
2. the declared-secret scope,
3. the headless flag.

Secret scoping (`NUVEL_CRON_SCOPE_SECRETS=1`) restricts the job's visible env to the
names its manifest declared, via `resolve_cron_env` / `active_cron_env`.

The headless policy (`NUVEL_CRON_HEADLESS_POLICY`) gets emphasis because its default
is surprising:

- `allow-shell` (**default**) — shell/bin tools are auto-allowed inside the isolated
  scope; **every other tool is auto-denied** with a logged reason.
- `deny-all` — everything denied.
- `allow-all` — everything allowed; opts out of the gate.

A cron job that needs to make an HTTP call will therefore be silently denied under
the default. This is called out prominently, not left to the table.

Also covers `CronIsolationPlugin` being inert outside cron runs
(`active_cron_run() is None`), so interactive turns are never affected, and the
HITL-gated creation path (`NUVEL_CRON_HITL_CREATE`).

"When NOT to use": `allow-all` in production; treating the gate as a substitute for
credential scoping rather than a complement to it.

#### 3. `adk-org-memory-retrieval`

~200 lines + `references/hybrid-ranking.md` + `references/knowledge-graph-schema.md`.

Wiring first, because that is what a reader needs before anything else: ADK's
official service registry via `register_org_memory_scheme()`, then
`memory_service_uri="nuvel-org-memory://default"` — no monkey-patching, the same
mechanism ADK uses for built-in `agentengine://` and `rag://` schemes. Three env
vars (`NUVEL_ORG_MEMORY_DSN`, `NUVEL_ORG_GRAPH_PATH`, `NUVEL_ORG_MEMORY_URI`). The
standalone `build_default_service()` path remains for scripts, batch jobs and evals.

Then the retrieval stack:

- **Hybrid RRF fusion** of a SQL keyword arm and a vector arm (both in
  `postgres_store.py`), with the ranking logic kept pure and side-effect-free in
  `hybrid.py` so it unit-tests without a database.
- **The tier boost as the cascade's first stage** — nuvel's actual divergence from
  gbrain, because nuvel has a scope hierarchy (user > team > … > org) where gbrain
  does not. This is the single most important conceptual point in the skill.
- **Bounded, floor-gated boost stages** (factors kept in roughly `[1.0, 1.6]`
  because unbounded multipliers can catastrophically flip rankings), **autocut**
  score-cliff result sizing, and dedup.
- **Zero-LLM knowledge graph**: `extract_entity_links` runs over content text on
  every write so the graph self-wires from prose — verb regexes with precedence
  `founded > invested_in > advises > works_at`, plus a bare-mention scan. Schema in
  migrations `0001_init.sql` / `0002_entity_links.sql`.
- **Relational recall**: `parse_relational_query` detects relationship questions
  ("who founded Acme") deterministically — regex only, no LLM, ReDoS-bounded seed
  captures — then retrieves over typed edges.
- **Synthesis**: `synthesize` and `analyze_gaps` as a thin pass over already-ranked
  rows that never re-ranks or replaces search.

Credits gbrain by name with its licence, pointing at `THIRD_PARTY.md`.

"When NOT to use": single-user agents (ADK's built-in memory suffices); no
Postgres + pgvector available; latency-critical paths.

#### 4. `adk-memory-self-improvement`

~200 lines + `references/fork-lifecycle.md`.

**Marked experimental in the frontmatter description and in the first paragraph.**
Every feature here is opt-in and defaults off; the skill's job is to explain the
mechanism honestly and let a reader decide, not to drive adoption.

Covers the template `memory/` package: `preload` (relevance-conditioned surfacing),
`consolidation` (the periodic store-agnostic "dream" pass — dedupe and merge),
`review_fork` (the after-turn judge fork), `sibling_runner` (fire-and-forget sibling
agent runs), `throttle` (per-session throttle for after-turn forks), `profile`
(structured per-user profile), `skill_review` plus the skill curator (which can
propose `SKILL.md` files), and `org_backend` (optional OrgMemoryService retrieval
backend, linking to skill #3).

The load-bearing section is **cost**: each enabled fork adds LLM calls per turn;
forks are fire-and-forget so their latency is off the critical path but their spend
is real; `NUVEL_MEMORY_FORK_CAP` and `NUVEL_MEMORY_FORK_COOLDOWN` are the safety
valves; `NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT` bounds shutdown. The skill states
explicitly: do not enable in production without eval coverage, and points at
`nuvel eval`.

"When NOT to use": cost-sensitive or high-QPS deployments; stateless task bots;
anything requiring deterministic behaviour across deploys (the same caveat
`--persona` carries).

#### 5. `adk-long-horizon-sessions`

~170 lines.

The counterpart to skill #1: where guardrails are about **stopping** runaway
behaviour, this is about **surviving** a long run. Both come from PR #50, and the
harness groups them under one "long-horizon resilience config" heading, but they have
different failure modes and different knobs, so they get separate skills.

Covers three things wired in `harness.py`:

- **Resumability.** `ResumabilityConfig(is_resumable=...)` lets an interrupted long
  run resume instead of restarting. On by default; `RESUMABILITY=false` disables it
  for deployments that cannot persist the extra invocation state (a stateless demo,
  for instance). The skill explains the trade-off: resumable runs persist more state
  per invocation, which is the cost of not losing an hour of work to a restart.
- **Event compaction.** `EventsCompactionConfig` rolls old events into summaries so a
  long session doesn't exhaust the context window. It fires on a sliding window of
  user turns (`COMPACTION_INTERVAL`, default 8) with a small `COMPACTION_OVERLAP`
  (default 2) so consecutive summaries share context, and keeps the most recent
  `COMPACTION_RETENTION` events (default 20) verbatim. The skill explains why overlap
  matters — non-overlapping summaries lose the causal thread across a boundary — and
  how compaction interacts with the `ContextWindow` plugin.
- **How both interact with the cache-stable prompt tiers.** Compaction rewrites
  history, which is *session*-tier content; the stable tier must stay byte-identical
  regardless. This is the cross-cutting rule a reader needs and is the reason these
  three topics belong in one skill. Full treatment of the tier contract lives in
  `adk-prompt-engineering`; this skill states the interaction and links there.

"When NOT to use": short-lived request/response agents, where resumability is pure
overhead and compaction never triggers; deployments on stateless infrastructure that
cannot persist invocation state.

### Audit of the existing 10 ADK skills

Targeted patches only where the new subsystems change existing advice:

| Skill | Change |
|---|---|
| `adk-callbacks-hitl` | Note that `GuardrailsPlugin`, `exfil_guard` and `CronIsolationPlugin` are real callbacks in the shipped chain; add the halt latch as a worked state-key example |
| `adk-agent-patterns` | Loop patterns name halt guards as the runaway backstop |
| `adk-tool-creation` | Tools must expect denial from the command classifier and `exfil_guard` |
| `adk-skill-creation` | The curator can now propose `SKILL.md` files |
| `adk-skill-design-patterns` | Same, cross-referenced |
| `adk-composio-tool-router` | **Write the missing `references/composio-patterns.md`** — three sections promise depth; deleting the pointer is the lazier fix |
| **`adk-prompt-engineering`** | **Substantial new section: the 3-tier cache-stable prompt contract** (see below) |
| `adk-streaming`, `adk-task-delegation`, `adk-workflow-graphs` | Verify claims; no substantive change expected |

#### The 3-tier prompt contract (`adk-prompt-engineering`)

This is the largest single addition to an existing skill, and arguably the most
commercially significant undocumented behaviour in the release.
`prompt/instructions.py` assembles the system prompt in three tiers:

1. **Stable** — identity and persona, byte-identical across turns so the prompt
   prefix stays cache-hot.
2. **Session** — slow-changing: user profile plus retrieved memory.
3. **Volatile** — per-turn reminders, riding the tail.

The full prompt is an ordered concatenation, so a volatile change never perturbs the
stable prefix. `tests/test_prompt_tiers.py` pins this with 12 tests, including
`test_full_prompt_stable_prefix_survives_volatile_change` and
`test_session_tier_degrades_when_sources_fail`.

The skill must explain **why the ordering is a cost decision, not a formatting one**:
providers cache on prefix, and a cache hit costs a fraction of a fresh input token.
Putting anything per-turn near the front invalidates the cached prefix every turn and
silently multiplies input cost — which matters most for exactly the agents nuvel
targets, with long stable personas and heavy retrieved memory. The section documents
the tier ordering as a contract that agent authors must preserve when adding
instruction content, and notes the graceful-degradation behaviour when memory sources
fail.

### Documentation and release

- **`.claude/skills/nuvel/SKILL.md`** — skill count 10 → 15, add the five to the
  topic table, add a brief "operating a long-horizon agent" pointer. Keep growth
  modest; the file is 173 lines and is loaded eagerly.
- **`nuvel/backends/adk/templates/.env.example`** — add the 12 missing variables in
  their subsystem sections, each with its default and a one-line comment, matching the
  file's existing style. This is the canonical env reference and stays the only one.
- **`README.md`** — a short pointer to `.env.example` as the configuration reference,
  plus mention of the five new skills. Deliberately **no env table**: duplicating 61+
  variables would create a second surface to drift.
- **`CLAUDE.md`** — fix `8 / 6 / 5` → `15 / 6 / 5`; add guardrails, cron isolation
  and memory to the architecture notes. Specifically call out that **`guardrails/`
  is a third duplicated chain** (`nuvel/guardrails/` for the meta-agent,
  `templates/{{agent_package}}/guardrails/` copied into generated agents) — the file
  already warns about two plugin chains and the same trap now exists a third time.
- **`THIRD_PARTY.md`** — gbrain attribution: MIT, © 2026 Garry Tan,
  `https://github.com/garrytan/gbrain`, naming the four derived modules
  (`hybrid.py`, `relational.py`, `extraction.py`, `synthesis.py`) and stating that
  these are independent Python reimplementations of algorithm design, not copied source.
- **`CHANGELOG.md`** — new, Keep-a-Changelog format, backfilling `0.1.0` / `0.1.1` /
  `0.2.0` headings and a full `0.3.0` entry.
- **`pyproject.toml`** — `0.2.0` → `0.3.0`. SemVer MINOR: six PRs of
  backward-compatible new features, nothing removed (13,044 insertions vs 130
  deletions). `0.2.1` would misrepresent a feature release as a bugfix.

### Testing

The suite must stay green at its current **895 passed, 12 skipped**. These are docs,
so the substantive addition is a new integrity test.

**`tests/test_skills_integrity.py`** — the mechanism that prevents recurrence:

1. Every `references/<file>.md` cited in any `SKILL.md` exists on disk. (This bug
   class has already occurred once, in `adk-composio-tool-router`.)
2. Every `SKILL.md` has parseable frontmatter with non-empty `name` and `description`,
   and `name` matches its directory.
3. `nuvel skills list` reports the expected per-framework counts (15 / 6 / 5),
   so adding a skill without registering it fails.
4. **`.env.example` ↔ template code parity.** Every `os.getenv("VAR")` in
   `nuvel/backends/adk/templates/` has a corresponding entry in `.env.example`
   (catches the drift that produced this spec), and every variable in `.env.example`
   is actually read somewhere (catches documenting knobs that do nothing). Variables
   that are internal markers set by the runtime rather than user knobs — e.g.
   `NUVEL_CRON_RUNNING` and `NUVEL_CRON_RUNNING_ENV`, written by the scheduler — are
   held in a small, explicitly named allow-list so the test stays honest rather than
   being weakened by a broad regex exemption.

Rationale: today's gap is a drift problem. Hand-written docs decay the same way in
two months. An integrity test converts "remember to document it" into a failing
build, and it is also the reason to write the missing composio reference rather than
delete the pointer — the test then enforces that promises made in skills stay honest.

## Delivery

Two PRs, both authored as `Folken2 <folkenai21@gmail.com>` (`gh` must also be on the
Folken2 account — verify with `gh api user --jq .login`, since git identity and gh
identity are independent).

**PR 1 — `docs: four ADK knowledge skills for guardrails, cron isolation + org memory`**
The five new skills and their reference files, the 10-skill audit patches (including
the substantial 3-tier prompt-caching section in `adk-prompt-engineering`), the missing
`references/composio-patterns.md`, `THIRD_PARTY.md`, and `tests/test_skills_integrity.py`.

**PR 2 — `docs: v0.3.0 release prep`**
The 12 missing `.env.example` entries, README pointer, `CLAUDE.md` corrections,
`.claude/skills/nuvel/SKILL.md` refresh, `CHANGELOG.md`, version bump to `0.3.0`.

**Then, and only with explicit human confirmation:** push the `v0.3.0` tag.
`.github/workflows/release.yml` fires on `v*.*.*` and publishes to PyPI via trusted
publishing, then creates a GitHub release. **Pushing the tag is the release.** PyPI
permanently refuses re-uploads of a version, so a bad `v0.3.0` can only be yanked and
superseded by `0.3.1` — it cannot be re-tagged. The workflow does verify that the tag
matches the `pyproject.toml` version, which catches the most common mistake.

## Risks

| Risk | Mitigation |
|---|---|
| Documenting behaviour that doesn't exist | Distil from existing module docstrings, which are unusually thorough; the integrity test cross-checks env vars against code |
| `.env.example` goes stale again | Precisely what test item 4 prevents, and why no second env table is created |
| The 3-tier prompt contract gets broken by a future edit | Already pinned by `tests/test_prompt_tiers.py`; the skill documents it as a contract so authors know it is load-bearing |
| Skills grow too long to be useful | Cap `SKILL.md` bodies near the house range (80–286 lines observed) and push depth into `references/` |
| Misclassifying an internal marker as a user knob | Explicit allow-list, reviewed against `cron/scheduler.py` and `cron/service.py` |
| Experimental features read as recommended | Experimental status stated in frontmatter and first paragraph, with a cost section and an eval-coverage requirement |
