# v0.3.0 Knowledge-Layer Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every subsystem shipped between `v0.2.0` and `643994f` operable skill coverage, close the `.env.example` drift with a test that prevents recurrence, attribute gbrain, and release `v0.3.0`.

**Architecture:** Five new ADK knowledge skills split by audience and maturity, targeted patches to the existing ten, and one new integrity test that enforces `.env.example` ↔ template-code parity. Delivered as two PRs: knowledge layer first, release prep second. Skills are authoring-time documentation consumed via `nuvel skills list|search` — they are never copied into generated agents, so they cost generated agents zero runtime context.

**Tech Stack:** Python 3.11+, pytest (async auto-mode), PyYAML for frontmatter, Markdown skills in the Anthropic skills format.

## Global Constraints

- **Identity:** every commit and PR must be authored as `Folken2 <folkenai21@gmail.com>`. Verify with `git config user.name` and `gh api user --jq .login` (both must say Folken2 — they are independent). Repo-local git config is already pinned.
- **Branch:** `docs/v030-knowledge-layer` already exists off `main` at `643994f` with the spec committed as `82787d7`.
- **Baseline:** `python -m pytest tests/ -q` currently reports **895 passed, 12 skipped**. It must never drop below that count.
- **Skill format:** frontmatter `name` MUST equal the directory name (asserted by the new test), and `description` MUST be non-empty and written as trigger conditions ("Read when…").
- **House style for skills:** `SKILL.md` body between 80 and 300 lines; depth goes into `references/*.md`. Every subsystem skill ends with a **"When NOT to use"** section and a **Quick reference**.
- **No new env table:** `.env.example` is the single canonical env reference. Do not add an env-var table to `README.md`.
- **Version:** `0.2.0` → `0.3.0` (SemVer MINOR: backward-compatible features, nothing removed).
- **Release is irreversible:** pushing a `v*.*.*` tag triggers `.github/workflows/release.yml`, which publishes to PyPI. Do NOT push the tag without explicit human confirmation.
- **No credentials as literals:** any DSN/key/token in docs or tests uses a `$ENV_VAR` placeholder.

## File Structure

**New skills** (each `nuvel/backends/adk/skills/<slug>/SKILL.md`):

| Slug | Responsibility | References |
|---|---|---|
| `adk-long-horizon-guardrails` | Stopping runaway behaviour: halt latch, no-progress, repeated-failure, command safety, exfil | `halt-latch-internals.md` |
| `adk-cron-isolation` | Blast-radius control for scheduled runs | — |
| `adk-org-memory-retrieval` | OrgMemoryService wiring + hybrid RRF/KG/relational retrieval | `hybrid-ranking.md`, `knowledge-graph-schema.md` |
| `adk-memory-self-improvement` | Experimental: consolidation, forks, curator | `fork-lifecycle.md` |
| `adk-long-horizon-sessions` | Surviving long runs: resumability, event compaction | — |

**New non-skill files:**

- `nuvel/backends/adk/skills/adk-composio-tool-router/references/composio-patterns.md` — fills the one dangling reference
- `THIRD_PARTY.md` — gbrain MIT attribution
- `tests/test_skills_integrity.py` — the anti-drift test
- `CHANGELOG.md` — Keep-a-Changelog

**Modified:** `adk-prompt-engineering/SKILL.md` (3-tier section), `adk-callbacks-hitl`, `adk-agent-patterns`, `adk-tool-creation`, `adk-skill-creation`, `adk-skill-design-patterns`, `.claude/skills/nuvel/SKILL.md`, `nuvel/backends/adk/templates/.env.example`, `README.md`, `CLAUDE.md`, `pyproject.toml`.

---

# PR 1 — Knowledge skills

### Task 1: Skills integrity test (references + frontmatter) and the missing composio reference

The test comes first and must fail on a real pre-existing bug: `adk-composio-tool-router/SKILL.md:106` promises `references/composio-patterns.md`, which does not exist.

**Files:**
- Create: `tests/test_skills_integrity.py`
- Create: `nuvel/backends/adk/skills/adk-composio-tool-router/references/composio-patterns.md`

**Interfaces:**
- Produces: module-level helpers `_all_skill_dirs()`, `_frontmatter(path)`, and constants `FRAMEWORK_DIRS`, `EXPECTED_SKILL_COUNTS`, `REFERENCE_RE`. Tasks 9 and 10 add tests to this same file and reuse these helpers.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_integrity.py`:

```python
"""Integrity tests for the bundled knowledge skills and template env surface.

These exist because four PRs between v0.2.0 and 643994f shipped subsystems with no
skill coverage and 18 environment variables with no `.env.example` entry. They turn
"remember to document it" into a failing build.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKENDS = REPO_ROOT / "nuvel" / "backends"

FRAMEWORK_DIRS = {
    "adk": BACKENDS / "adk" / "skills",
    "claude_agent_sdk": BACKENDS / "claude_agent_sdk" / "skills",
    "anthropic_managed_agents": BACKENDS / "anthropic_managed_agents" / "skills",
}

# Updated by Task 9 once the five new ADK skills exist.
EXPECTED_SKILL_COUNTS = {"adk": 10, "claude_agent_sdk": 6, "anthropic_managed_agents": 5}

REFERENCE_RE = re.compile(r"references/([a-z0-9][a-z0-9-]*\.md)")


def _skill_dirs(framework: str) -> list[Path]:
    root = FRAMEWORK_DIRS[framework]
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def _all_skill_dirs() -> list[Path]:
    out: list[Path] = []
    for framework in FRAMEWORK_DIRS:
        out.extend(_skill_dirs(framework))
    return out


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_md} has no YAML frontmatter"
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("---")
    return yaml.safe_load(front) or {}


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_referenced_files_exist(skill_dir: Path) -> None:
    """A SKILL.md must not promise a reference file that isn't on disk."""
    cited = set(REFERENCE_RE.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8")))
    missing = sorted(n for n in cited if not (skill_dir / "references" / n).is_file())
    assert not missing, f"{skill_dir.name} cites missing reference files: {missing}"


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_frontmatter_is_valid(skill_dir: Path) -> None:
    """Every skill needs a name matching its directory and a non-empty description."""
    meta = _frontmatter(skill_dir / "SKILL.md")
    name = meta.get("name")
    description = str(meta.get("description", "")).strip()
    assert name, f"{skill_dir.name}: frontmatter 'name' is missing or empty"
    assert description, f"{skill_dir.name}: frontmatter 'description' is missing or empty"
    assert name == skill_dir.name, (
        f"{skill_dir.name}: frontmatter name {name!r} does not match directory name"
    )
```

- [ ] **Step 2: Run the test to verify it fails for the right reason**

Run: `python -m pytest tests/test_skills_integrity.py -q`

Expected: exactly **one** failure —
`test_referenced_files_exist[adk-composio-tool-router]` with
`cites missing reference files: ['composio-patterns.md']`.
All 20 other parametrised reference cases and all 21 frontmatter cases pass.
(Frontmatter validity across all 21 skills was verified before this plan was written; if any frontmatter case fails, stop and report — do not weaken the assertion.)

- [ ] **Step 3: Write the missing reference file**

Create `nuvel/backends/adk/skills/adk-composio-tool-router/references/composio-patterns.md`. `SKILL.md:106` promises: *"multi-tenant request routing, service-account vs end-user-account models, Composio with `--persona` agents, filtering toolkits per agent, and debugging 'tool not found' errors."* Deliver exactly those five sections, in that order, with `##` headings:

1. `## Multi-tenant request routing` — building the toolset per request from the authenticated user id rather than the `COMPOSIO_USER_ID` env var; show the `get_agent_for_user(end_user_id)` shape from `SKILL.md:73-83` and note session creation is fast enough to do per request.
2. `## Service-account vs end-user-account models` — the trade-off from `SKILL.md:87`: one shared connection set is simpler but gives no isolation; use it only when the agent acts on its own data.
3. `## Composio with --persona agents` — a persona agent's self-authored skills may include managing connections; caution that a self-rewriting agent holding live OAuth connections widens blast radius, and pair it with `adk-long-horizon-guardrails`.
4. `## Filtering toolkits per agent` — the MCP transport has no per-tool gating (`SKILL.md:63`), so filtering happens by connecting only what the agent should reach, per `user_id`.
5. `## Debugging "tool not found"` — ordered checklist: is `COMPOSIO_API_KEY` set (else the toolset no-ops silently, `SKILL.md:28`); was the toolkit connected for *this* `user_id`; does the tool name match `<TOOLKIT>_<ACTION>`; did the OAuth flow complete.

Keep it 80–150 lines. Do not invent Composio API surface beyond what `SKILL.md` already documents.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_skills_integrity.py -q`
Expected: all cases PASS (42 tests: 21 reference + 21 frontmatter).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: **937 passed, 12 skipped** (895 + 42 new).

- [ ] **Step 6: Commit**

```bash
git add tests/test_skills_integrity.py \
  nuvel/backends/adk/skills/adk-composio-tool-router/references/composio-patterns.md
git commit -m "test(skills): assert reference files exist and frontmatter is valid

Adds tests/test_skills_integrity.py, which immediately caught a real dangling
pointer: adk-composio-tool-router/SKILL.md:106 promised references/composio-patterns.md
and it was never written. Writes that reference file to make the suite green."
```

---

### Task 2: gbrain attribution

**Files:**
- Create: `THIRD_PARTY.md`

**Interfaces:**
- Produces: `THIRD_PARTY.md`, linked from `adk-org-memory-retrieval/SKILL.md` in Task 5.

- [ ] **Step 1: Verify the upstream facts still hold**

Run: `gh api repos/garrytan/gbrain --jq '.license.spdx_id, .html_url'`
Expected: `MIT` and `https://github.com/garrytan/gbrain`.

- [ ] **Step 2: Write `THIRD_PARTY.md`**

```markdown
# Third-party attributions

nuvel is MIT licensed (see `LICENSE`). It also carries work derived from the
following MIT-licensed projects.

## gbrain

- **Project:** [garrytan/gbrain](https://github.com/garrytan/gbrain) — "Garry's Opinionated OpenClaw/Hermes Agent Brain"
- **Licence:** MIT, Copyright (c) 2026 Garry Tan
- **Language:** TypeScript

nuvel's org-memory retrieval stack is an independent Python/SQL reimplementation of
algorithm designs originating in gbrain. No gbrain source is vendored or copied; these
are Python modules written against the same algorithmic ideas, adapted to nuvel's
scope-hierarchy memory model (which gbrain does not have).

| nuvel module | Derived design |
|---|---|
| `nuvel/memory/hybrid.py` | RRF fusion, cosine blend, floor-gated boost cascade, autocut, dedup (`hybrid.ts`) |
| `nuvel/memory/relational.py` | Relational intent detection and typed-edge recall (`relational-intent.ts`, `relational-recall.ts`) |
| `nuvel/memory/extraction.py` | Verb-regex link-type inference and bare-mention scanning (`link-extraction.ts`) |
| `nuvel/memory/synthesis.py` | Answer synthesis over ranked rows rather than returning raw pages |

Reciprocal Rank Fusion itself is published prior art — Cormack, Clarke & Büttcher,
*Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*
(SIGIR 2009) — and is used here on those terms.
```

- [ ] **Step 3: Verify no credentials or private identifiers leaked**

Run: `grep -nE 'postgres://|postgresql://|sk-|gho_|ghp_' THIRD_PARTY.md || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 4: Commit**

```bash
git add THIRD_PARTY.md
git commit -m "docs: attribute gbrain (MIT) for org-memory retrieval designs

nuvel/memory/{hybrid,relational,extraction,synthesis}.py describe themselves as
reimplementations of garrytan/gbrain. gbrain is MIT (c) 2026 Garry Tan and nuvel is
MIT, so the licences are compatible; this records the attribution explicitly."
```

---

### Task 3: Skill — `adk-long-horizon-guardrails`

**Files:**
- Create: `nuvel/backends/adk/skills/adk-long-horizon-guardrails/SKILL.md`
- Create: `nuvel/backends/adk/skills/adk-long-horizon-guardrails/references/halt-latch-internals.md`

**Source of truth (read these before writing; do not invent behaviour):**
`nuvel/guardrails/__init__.py` (the two families), `halt_consumer.py`, `no_progress.py`, `repeated_failure.py`, `guardrails_plugin.py`, `command_safety.py`, `command_classify.py`, `exfil_guard.py`. The generated-agent copies live at `nuvel/backends/adk/templates/{{agent_package}}/guardrails/` and are identical in behaviour.

**Interfaces:**
- Produces: skill slug `adk-long-horizon-guardrails`, referenced by `.claude/skills/nuvel/SKILL.md` (Task 11), by `adk-composio-tool-router/references/composio-patterns.md` §3 (Task 1), and cross-linked from `adk-callbacks-hitl` and `adk-agent-patterns` (Task 8).

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: adk-long-horizon-guardrails
description: Halt guards and command-safety guardrails for ADK agents — the shared halt latch, NoProgressGuard, RepeatedFailureGuard, the structural argv-level shell command classifier, and exfil_guard. Read when an agent will run unattended or for a long time, when a run needs a backstop against response loops or a tool retried identically forever, when an agent can execute shell commands, or when tool arguments might carry secrets. Also read when tuning EXFIL_GUARD_STRICT or debugging a "[halted: ...]" response.
---
```

- [ ] **Step 2: Write the body with these exact `##` sections**

1. `## The two families` — halt guards vs command/exfiltration guards, per `guardrails/__init__.py:3-11`. State that `GuardrailsPlugin` is wired unconditionally into every generated agent's plugin chain (`plugins/__init__.py.tmpl:54`), so this is default-on behaviour, not opt-in.
2. `## The halt latch` — the shared primitive, and lead with it because all halt guards funnel through it. `latch_halt(reason)` latches only if no halt is already set; `halt_consumer_callback` is a `before_model_callback` that short-circuits the model with the canonical `[halted: <reason>]` envelope (`halt_content`) while latched; `acknowledge_halt` clears the signal so the next model call runs; `reset_halt_handoff` clears the once-per-halt handoff flag at a user-turn boundary. Name the two state keys `HALT_REASON_STATE_KEY` and `HALT_HANDOFF_DELIVERED_STATE_KEY`.
3. `## NoProgressGuard` — `after_model_callback`; latches when the model emits byte-identical text `window` times consecutively, meaning a response loop burning tokens with nothing to show.
4. `## RepeatedFailureGuard` — `after_tool_callback`; fingerprint is `tool_name` plus a SHA-256 of canonical arguments; `threshold` consecutive failures of the same signature latches; a success clears that signature's streak. Note `LAST_ERROR_STATE_KEY`.
5. `## GuardrailsPlugin` — binds all three to one turn boundary so they observe the same notion of "turn".
6. `## Command safety is structural, not textual` — the key transferable insight. `command_safety.classify` lexes each segment into argv with `shlex` and inspects *tokens*, because substring/regex matching on the raw string is defeated by quoting and spacing. `segments` unwraps a single `bash -c '<inner>'` and recurses. Verdicts: `("deny", reason)` for catastrophic (e.g. `rm -rf /`), `("ask", reason)` for risky, allow otherwise; `classify` returns the strongest verdict across all segments. Mention the `command_classify` helpers (`strip_wrapper`, `split_segments`, `command_prefix`, `has_redirection`, `has_command_substitution`) and that `lex` returns `None` on a parse error.
7. `## exfil_guard` — `before_tool_callback` scanning tool arguments for high-confidence secret patterns (cloud keys, private-key blocks, provider tokens), catching the shape where the model pastes a credential it read from the environment into an outbound call. `EXFIL_GUARD_STRICT` defaults to `1` (block); lax mode flags instead.
8. `## When NOT to use` — short interactive agents where a latched halt is user-visible friction; agents whose legitimate output is genuinely repetitive (raise `window` rather than disabling); a tool that legitimately retries identical calls (raise `threshold`). Warn that disabling `exfil_guard` strictness is a security decision, not a convenience one.
9. `## Quick reference` — env vars (`EXFIL_GUARD_STRICT`), the state keys, and a table mapping guard → callback hook.

- [ ] **Step 3: Write `references/halt-latch-internals.md`**

Cover: exact state keys and their values; callback ordering within a turn (`before_model` → model → `after_model`; `before_tool` → tool → `after_tool`); why `latch_halt` is first-write-wins (so the earliest, most specific reason survives); the handoff flag's once-per-halt semantics and where `reset_halt_handoff` is called; and a worked example of authoring a custom guard that latches correctly — an `after_tool_callback` that latches when a budget is exceeded. 100–180 lines.

- [ ] **Step 4: Verify the integrity test still passes**

Run: `python -m pytest tests/test_skills_integrity.py -q`
Expected: PASS, now with 22 skills parametrised per test (43 → 44 cases). The frontmatter case for the new skill must pass, proving `name` matches the directory.

- [ ] **Step 5: Verify the skill is discoverable**

Run: `python -m nuvel.cli skills search halt` (or `nuvel skills search halt`)
Expected: `adk-long-horizon-guardrails` appears in the output.

- [ ] **Step 6: Commit**

```bash
git add nuvel/backends/adk/skills/adk-long-horizon-guardrails
git commit -m "docs(skills): add adk-long-horizon-guardrails

Documents PR #50's halt guards (halt latch, NoProgressGuard, RepeatedFailureGuard,
GuardrailsPlugin) and the command/exfiltration guards, including why the shell
command classifier works at argv level rather than on the raw string."
```

---

### Task 4: Skill — `adk-cron-isolation`

**Files:**
- Create: `nuvel/backends/adk/skills/adk-cron-isolation/SKILL.md`

**Source of truth:** `nuvel/backends/adk/templates/{{agent_package}}/cron/isolation.py`, `plugins/cron_isolation_plugin.py`, `cron/service.py`, `cron/tools.py`, `cron/scheduler.py`, and the `# ── Cron scheduling ──` / `# ── Cron isolation hardening (opt-in) ──` sections of `.env.example` (lines 177 and 190).

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: adk-cron-isolation
description: Blast-radius control for scheduled (cron) runs of an ADK agent — scoped secrets, the headless tool-approval policy, and HITL-gated job creation. Read when an agent runs jobs on a schedule with no human present, when a cron job needs credentials but shouldn't see every env var, when a scheduled tool call is being denied unexpectedly, or when tuning NUVEL_CRON_HEADLESS_POLICY / NUVEL_CRON_SCOPE_SECRETS / NUVEL_CRON_HITL_CREATE.
---
```

- [ ] **Step 2: Write the body with these exact `##` sections**

1. `## Why scheduled runs need their own rules` — a cron job runs unattended with nobody to approve tool calls, so it needs a bounded blast radius (`isolation.py:1-6`).
2. `## The three markers` — `cron_isolation()` installs three async-local `ContextVar` markers around a job's invocation and resets them on exit: the cron-run marker (which `job_id`, read via `active_cron_run()`), the declared-secret scope (`active_secret_scope()`), and the headless flag (`is_headless()`).
3. `## Scoped secrets` — with `NUVEL_CRON_SCOPE_SECRETS=1`, only the env-var names the job's manifest declared are visible; `resolve_cron_env(declared)` computes that mapping and `active_cron_env()` returns it inside a scoped run. Explain the threat model: a scheduled job that only needs one API key should not be able to read every credential in the process.
4. `## The headless policy — read this before deploying` — `NUVEL_CRON_HEADLESS_POLICY` (`cron_isolation_plugin.py:1-14`):
   - `allow-shell` (**default**) — shell/bin tools run inside the isolated scope and are auto-allowed; **every other tool is auto-denied with a logged reason**.
   - `deny-all` — every tool denied.
   - `allow-all` — every tool allowed; opts out of the gate entirely.
   State plainly that under the default, a cron job that makes an HTTP call or a DB write **will be denied**, and that this is the single most common surprise. Point at `NUVEL_CRON_SHELL_TOOLS` / `shell_tool_names()` / `is_shell_tool()` for what counts as a shell tool, and `evaluate_headless_tool()` as the decision function.
5. `## Inert outside cron runs` — `CronIsolationPlugin` no-ops when `active_cron_run()` is `None`, so ordinary interactive turns are never gated. This is why the plugin ships wired-in by default.
6. `## HITL-gated job creation` — `NUVEL_CRON_HITL_CREATE` requires human approval before a new scheduled job is registered, so the agent cannot silently give itself recurring unattended execution.
7. `## When NOT to use` — `allow-all` in production defeats the whole mechanism; scoping secrets is not a substitute for least-privilege credentials upstream, it is a second layer; if a job genuinely needs broad tool access, prefer narrowing the job to shell tools that call a vetted script over switching to `allow-all`.
8. `## Quick reference` — a table of all cron env vars with defaults, taken verbatim from `.env.example` lines 177-211. Mark `NUVEL_CRON_RUNNING` and `NUVEL_CRON_RUNNING_ENV` as runtime markers set by the scheduler, not user knobs.

- [ ] **Step 3: Verify env var claims against code**

Run:
```bash
grep -rn 'NUVEL_CRON_' nuvel/backends/adk/templates/'{{agent_package}}'/cron/ \
  nuvel/backends/adk/templates/'{{agent_package}}'/plugins/cron_isolation_plugin.py
```
Confirm every variable named in the skill appears here or in `.env.example`, and that no variable is described with a default the code contradicts.

- [ ] **Step 4: Run the integrity test and full suite**

Run: `python -m pytest tests/test_skills_integrity.py tests/ -q`
Expected: full suite still ≥ 937 passed, 12 skipped.

- [ ] **Step 5: Commit**

```bash
git add nuvel/backends/adk/skills/adk-cron-isolation
git commit -m "docs(skills): add adk-cron-isolation

Documents PR #51: the three ContextVar markers, secret scoping, and the headless
tool policy — including that allow-shell (the default) auto-denies every non-shell
tool, which is the most common deployment surprise."
```

---

### Task 5: Skill — `adk-org-memory-retrieval`

**Files:**
- Create: `nuvel/backends/adk/skills/adk-org-memory-retrieval/SKILL.md`
- Create: `nuvel/backends/adk/skills/adk-org-memory-retrieval/references/hybrid-ranking.md`
- Create: `nuvel/backends/adk/skills/adk-org-memory-retrieval/references/knowledge-graph-schema.md`

**Source of truth:** `nuvel/memory/adk_registry.py`, `factory.py`, `org_memory_service.py`, `hybrid.py`, `relational.py`, `extraction.py`, `synthesis.py`, `backends/postgres_store.py`, `backends/migrations/0001_init.sql`, `backends/migrations/0002_entity_links.sql`, plus `docs/memory/org-memory-service.md` and `nuvel/run_adk.py` for the wiring path.

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: adk-org-memory-retrieval
description: Scope-aware hierarchical memory for ADK agents via OrgMemoryService — wiring it through ADK's service registry with three env vars, plus how retrieval works (hybrid RRF fusion of keyword and vector arms, tier boost, floor-gated boost cascade, autocut, a zero-LLM knowledge graph, relational recall, and answer synthesis). Read when an agent needs memory shared across users/teams/an org rather than per-session, when wiring NUVEL_ORG_MEMORY_URI / NUVEL_ORG_MEMORY_DSN / NUVEL_ORG_GRAPH_PATH, when tuning retrieval quality, or when deciding whether org memory is warranted at all.
---
```

- [ ] **Step 2: Write the body with these exact `##` sections**

1. `## What it is` — memory scoped along a hierarchy (user > team > … > org) rather than per-session, so knowledge written once at a higher scope is retrievable by everyone beneath it.
2. `## Wiring (three env vars)` — this comes first because it is what a reader needs before anything else. `nuvel.memory.adk_registry.register_org_memory_scheme()` registers a factory under the `nuvel-org-memory` scheme in ADK's official service registry (`google.adk.cli.service_registry.register_memory_service`), after which `get_fast_api_app(memory_service_uri="nuvel-org-memory://default")` constructs it natively — the same mechanism ADK uses for built-in `agentengine://` and `rag://`. **No monkey-patching.** Show:

```bash
export NUVEL_ORG_MEMORY_DSN=$NUVEL_ORG_MEMORY_DSN   # postgres DSN, never inline a literal
export NUVEL_ORG_GRAPH_PATH=/path/to/org_graph.yaml
export NUVEL_ORG_MEMORY_URI=nuvel-org-memory://default
```

   Note that `factory.build_default_service()` remains the standalone path for scripts, batch jobs and evals, and that the service degrades to `None` when no DSN is configured (the agent then relies on its markdown memory store — see `templates/{{agent_package}}/memory/org_backend.py`).
3. `## Hybrid retrieval` — a SQL keyword arm and a vector arm (both in `postgres_store.py`) fused by Reciprocal Rank Fusion, `score = sum(1 / (RRF_K + rank))`. Emphasise that `hybrid.py` holds only pure, side-effect-free ranking logic so it unit-tests without a database.
4. `## The tier boost is nuvel's divergence` — the most important conceptual point. The boost cascade's first stage is a **scope-tier boost** (user > team > … > org), because nuvel has a scope hierarchy where gbrain does not. Later stages are bounded and floor-gated (`compute_floor_threshold`), with factors kept in roughly `[1.0, 1.6]` — explain that unbounded multipliers can catastrophically flip rankings, which is why the bound exists.
5. `## Autocut and dedup` — score-cliff result sizing (`apply_autocut`) rather than a fixed top-N, plus dedup.
6. `## The knowledge graph self-wires` — `extract_entity_links` runs over content text on **every write**, with no LLM: verb regexes plus a bare-mention scan, precedence `founded > invested_in > advises > works_at`, precision-first stopword seeding. Schema in `0001_init.sql` and `0002_entity_links.sql`.
7. `## Relational recall` — `parse_relational_query` detects relationship questions ("who founded Acme", "founders of Acme", "who works at Globex") deterministically: regex only, no LLM, ReDoS-bounded seed captures. Then retrieval walks typed edges.
8. `## Synthesis and gap analysis` — `synthesize` turns top-N ranked rows into a prose answer with `Citation`s; `analyze_gaps` reports what the store could not answer. Both are a **thin pass over already-ranked rows — they never re-rank or replace search.**
9. `## Attribution` — the retrieval design derives from [garrytan/gbrain](https://github.com/garrytan/gbrain) (MIT, © 2026 Garry Tan); RRF itself is Cormack, Clarke & Büttcher (SIGIR 2009). Link `THIRD_PARTY.md`.
10. `## When NOT to use` — a single-user agent (ADK's built-in memory suffices and org memory is pure overhead); no Postgres with pgvector available; latency-critical paths where an extra fused query is unacceptable; cases where a markdown memory store is genuinely enough.
11. `## Quick reference` — the three env vars, the registry call, and a concept→API table.

- [ ] **Step 3: Write `references/hybrid-ranking.md`**

The RRF formula and the meaning of `RRF_K`; the cosine blend; each cascade stage in order with its floor gate and factor bound; `compute_floor_threshold` semantics; `apply_autocut`'s score-cliff rule; dedup; and a tuning guide — which knob to move for "results are too few", "an irrelevant high-tier memory dominates", "vector arm swamps keyword arm". 150–250 lines. Cite `hybrid.py` line numbers.

- [ ] **Step 4: Write `references/knowledge-graph-schema.md`**

Tables and columns from `0001_init.sql` and `0002_entity_links.sql`; the typed edge kinds and their precedence; how `extract_entity_links` normalises entity basenames (`normalize_basename` role); what the stopword seeding excludes and why precision-first; and how to add a new edge type (regex, precedence position, migration). 100–200 lines.

- [ ] **Step 5: Verify no credential literals**

Run: `grep -rnE 'postgres(ql)?://[^$]' nuvel/backends/adk/skills/adk-org-memory-retrieval/ || echo CLEAN`
Expected: `CLEAN` — every DSN must be a `$ENV_VAR` placeholder.

- [ ] **Step 6: Run the integrity test and full suite**

Run: `python -m pytest tests/ -q`
Expected: ≥ 937 passed, 12 skipped. The reference-existence test must pass for both new reference files.

- [ ] **Step 7: Commit**

```bash
git add nuvel/backends/adk/skills/adk-org-memory-retrieval
git commit -m "docs(skills): add adk-org-memory-retrieval

Documents PRs #54/#55: service-registry wiring, hybrid RRF retrieval with the
scope-tier boost as first cascade stage, the zero-LLM knowledge graph, relational
recall, and synthesis. Credits gbrain (MIT) for the retrieval design."
```

---

### Task 6: Skill — `adk-memory-self-improvement`

**Files:**
- Create: `nuvel/backends/adk/skills/adk-memory-self-improvement/SKILL.md`
- Create: `nuvel/backends/adk/skills/adk-memory-self-improvement/references/fork-lifecycle.md`

**Source of truth:** `nuvel/backends/adk/templates/{{agent_package}}/memory/` — `consolidation.py`, `preload.py`, `profile.py`, `review_fork.py`, `sibling_runner.py`, `skill_review.py`, `throttle.py`, `fork_utils.py`, `org_backend.py` — plus `plugins/skill_curator_plugin.py` and `.env.example` lines 88-119.

**This skill is EXPERIMENTAL and must say so in the frontmatter and the first paragraph.** Every feature is opt-in and defaults off. The skill's job is to explain the mechanism and its cost honestly, not to drive adoption.

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: adk-memory-self-improvement
description: EXPERIMENTAL, opt-in memory self-improvement for long-lived ADK agents — relevance-conditioned preload, the periodic consolidation "dream" pass, the after-turn judge fork, fire-and-forget sibling runs, per-session throttling, and the skill curator that proposes SKILL.md files. Read before enabling any NUVEL_MEMORY_* or NUVEL_SKILL_CURATOR* flag, when budgeting the extra LLM calls these add per turn, or when an agent should improve its own memory and skills over months. Every feature here defaults off and none should reach production without eval coverage.
---
```

- [ ] **Step 2: Write the body with these exact `##` sections**

1. `## Status: experimental` — first paragraph. State that these features are opt-in, default off, add LLM calls per turn, and should not be enabled in production without eval coverage via `nuvel eval`. Say what "experimental" means concretely here: the mechanisms are unit-tested, but their effect on answer quality over long horizons is unproven.
2. `## Relevance-conditioned preload` — `NUVEL_MEMORY_PRELOAD` (default `1`, on) surfaces only the most relevant memory chunks instead of injecting whole files; `NUVEL_MEMORY_PRELOAD_TOP_K` (default `5`) caps chunks per turn. Note this one is on by default and is the cheapest of the set — it *reduces* tokens.
3. `## The consolidation "dream" pass` — `NUVEL_MEMORY_CONSOLIDATION` gates a periodic, store-agnostic dedupe-and-merge over accumulated memories; `NUVEL_MEMORY_CONSOLIDATION_INTERVAL` sets cadence and `NUVEL_MEMORY_CONSOLIDATION_SIM` the similarity threshold for treating two memories as duplicates. Explain the failure mode of setting the threshold too low: distinct memories get merged and information is lost irreversibly.
4. `## The after-turn judge fork` — `NUVEL_MEMORY_REVIEW_FORK` runs a judge after a turn completes to critique and improve what was stored. It is a *fork*: it does not block the user's response.
5. `## Sibling runs and draining` — `sibling_runner` launches fire-and-forget sibling agent invocations; `NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT` bounds how long shutdown waits for in-flight runs. Warn that a too-short drain silently discards work and a too-long drain delays deploys.
6. `## Throttling is the safety valve` — `NUVEL_MEMORY_FORK_CAP` and `NUVEL_MEMORY_FORK_COOLDOWN` bound forks per session and enforce a gap between them. Present these as required companions to enabling any fork, not optional extras.
7. `## The skill curator` — `NUVEL_SKILL_CURATOR` (default **off**) lets the agent propose its own `SKILL.md` files from observed tool usage. Thresholds: `NUVEL_SKILL_CURATOR_MIN_TOOLS` (default `5`), `NUVEL_SKILL_CURATOR_MIN_EVENTS` (default `12`), `NUVEL_SKILL_CURATOR_MIN_ERRORS` (default `3`); `NUVEL_SKILL_CURATOR_MODEL` (default `gemini-2.0-flash`); `NUVEL_SKILL_PROPOSALS_DIR` and `NUVEL_SKILLS_DIR` override paths. State that proposals are *proposals* — a human should review before they become live skills — and cross-reference `adk-skill-creation`.
8. `## Cost model` — the load-bearing section. Each enabled fork adds LLM calls per turn. Forks are fire-and-forget, so their *latency* is off the critical path but their *spend* is not. Give the arithmetic shape: with a judge fork enabled and no throttle, worst case is one extra model call per turn per fork type, so a 100-turn session can double or triple its model calls. Point at `CostGuardPlugin` and `nuvel traces` / `nuvel pricing` for measuring it, and state the rule: enable one flag at a time and measure before adding the next.
9. `## When NOT to use` — cost-sensitive or high-QPS deployments; stateless task bots; anything requiring deterministic behaviour across deploys (same caveat `--persona` carries — a support bot that rewrites its own memory mid-conversation is a regression, not a feature); any deployment without eval coverage.
10. `## Quick reference` — a table of all 8 `NUVEL_MEMORY_*` and 6 curator variables with exact defaults.

- [ ] **Step 3: Write `references/fork-lifecycle.md`**

The precise lifecycle of an after-turn fork: trigger point, throttle check order (cap then cooldown), what state a fork sees, how results are written back, and drain-at-shutdown semantics. Include the interaction rule: forks must not mutate the stable prompt tier (see `adk-prompt-engineering`). Document what happens when a fork fails — it must never fail the user's turn. 120–200 lines.

- [ ] **Step 4: Verify every documented default matches code**

Run:
```bash
grep -rnE 'DEFAULT_(MIN_TOOLS|MIN_EVENTS|MIN_ERRORS|MODEL|TOP_K)' \
  nuvel/backends/adk/templates/'{{agent_package}}'/plugins/skill_curator_plugin.py \
  nuvel/backends/adk/templates/'{{agent_package}}'/memory/preload.py
```
Expected: `DEFAULT_MIN_TOOLS = 5`, `DEFAULT_MIN_EVENTS = 12`, `DEFAULT_MIN_ERRORS = 3`, `DEFAULT_MODEL = "gemini-2.0-flash"`. Correct the skill if any differ.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: ≥ 937 passed, 12 skipped.

- [ ] **Step 6: Commit**

```bash
git add nuvel/backends/adk/skills/adk-memory-self-improvement
git commit -m "docs(skills): add adk-memory-self-improvement (experimental)

Documents PR #50's opt-in memory self-improvement layer: preload, consolidation,
judge fork, sibling runs, throttling, and the skill curator. Marked experimental,
with an explicit cost model and a requirement for eval coverage before production."
```

---

### Task 7: Skill — `adk-long-horizon-sessions`

**Files:**
- Create: `nuvel/backends/adk/skills/adk-long-horizon-sessions/SKILL.md`

**Source of truth:** `nuvel/backends/adk/templates/{{agent_package}}/harness.py.tmpl` (the `resumability_config` and `compaction_config` properties and their docstrings), `google.adk.apps._configs.ResumabilityConfig` / `EventsCompactionConfig`, and `tests/test_prompt_tiers.py` for the tier interaction.

- [ ] **Step 1: Write the frontmatter**

```yaml
---
name: adk-long-horizon-sessions
description: Surviving long ADK runs — resumability so an interrupted invocation continues instead of restarting, and event compaction so a long session doesn't exhaust the context window. Read when an agent runs for hours, when a session's history is growing past the context window, when deciding whether RESUMABILITY should stay on for a stateless deployment, or when tuning COMPACTION_INTERVAL / COMPACTION_OVERLAP / COMPACTION_RETENTION. Pairs with adk-long-horizon-guardrails (which stops runaway runs) and adk-prompt-engineering (the cache-stable prompt tiers compaction interacts with).
---
```

- [ ] **Step 2: Write the body with these exact `##` sections**

1. `## Stopping versus surviving` — one short paragraph placing this skill against `adk-long-horizon-guardrails`: guardrails stop a run that has gone wrong; this skill keeps a run that is going *right* alive across interruptions and a growing history.
2. `## Resumability` — `ResumabilityConfig(is_resumable=...)`, driven by `RESUMABILITY` (default `true`). An interrupted long run resumes instead of restarting. Set `RESUMABILITY=false` when a deployment cannot persist the extra invocation state — a stateless demo, for instance. State the trade-off explicitly: resumable runs persist more state per invocation; that storage cost is what buys you not losing an hour of work to a restart.
3. `## Event compaction` — `EventsCompactionConfig` rolls old events into summaries so a long session doesn't exhaust the context window. Three knobs with exact defaults: `COMPACTION_INTERVAL` (default `8`) is the sliding window of user turns that triggers compaction; `COMPACTION_OVERLAP` (default `2`) makes consecutive summaries share context; `COMPACTION_RETENTION` (default `20`) keeps the most recent events verbatim. Explain **why overlap matters**: non-overlapping summaries lose the causal thread across a boundary, so a later turn can no longer see why an earlier decision was made.
4. `## Compaction versus the ContextWindow plugin` — they solve adjacent problems and both are active by default. Compaction rewrites history into summaries; `ContextWindowPlugin` (see `CONTEXT_WINDOW_*` in `.env.example`) monitors and warns on window usage. Note `CONTEXT_FILTER_KEEP` as the third related knob.
5. `## Interaction with the cache-stable prompt tiers` — the cross-cutting rule and the reason these topics share a skill. Compaction rewrites *history*, which is session-tier content; the **stable tier must stay byte-identical regardless**, or the cached prompt prefix is invalidated and input cost rises. Full treatment of the tier contract lives in `adk-prompt-engineering`; link there.
6. `## When NOT to use` — short request/response agents where resumability is pure overhead and compaction never triggers; stateless infrastructure that cannot persist invocation state (turn resumability off deliberately rather than letting it fail); sessions short enough that compaction would summarise away context you still need.
7. `## Quick reference` — the four env vars with defaults, and the `harness.py` property names (`resumability_config`, `compaction_config`).

- [ ] **Step 3: Verify the defaults against the harness**

Run:
```bash
grep -nE 'RESUMABILITY|COMPACTION_' nuvel/backends/adk/templates/'{{agent_package}}'/harness.py.tmpl
```
Expected: `RESUMABILITY` default `"true"`, `COMPACTION_INTERVAL` `"8"`, `COMPACTION_OVERLAP` `"2"`, `COMPACTION_RETENTION` `"20"`. Correct the skill if any differ.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: ≥ 937 passed, 12 skipped.

- [ ] **Step 5: Commit**

```bash
git add nuvel/backends/adk/skills/adk-long-horizon-sessions
git commit -m "docs(skills): add adk-long-horizon-sessions

Documents the resumability and event-compaction configs PR #50 wired into
harness.py, and the rule that compaction must not perturb the stable prompt tier."
```

---

### Task 8: Patch the existing ADK skills

**Files:**
- Modify: `nuvel/backends/adk/skills/adk-prompt-engineering/SKILL.md` (substantial new section)
- Modify: `nuvel/backends/adk/skills/adk-callbacks-hitl/SKILL.md`
- Modify: `nuvel/backends/adk/skills/adk-agent-patterns/SKILL.md`
- Modify: `nuvel/backends/adk/skills/adk-tool-creation/SKILL.md`
- Modify: `nuvel/backends/adk/skills/adk-skill-creation/SKILL.md`
- Modify: `nuvel/backends/adk/skills/adk-skill-design-patterns/SKILL.md`

**Interfaces:**
- Consumes: the five new skill slugs from Tasks 3-7 (cross-reference them by exact slug).

- [ ] **Step 1: Add the 3-tier prompt-caching section to `adk-prompt-engineering`**

This is the largest edit and the most commercially significant undocumented behaviour in the release. Add a `## The three prompt tiers (cache stability)` section. Source of truth: `nuvel/backends/adk/templates/{{agent_package}}/prompt/instructions.py.tmpl` and the 12 tests in `tests/test_prompt_tiers.py`.

Content requirements:

- The prompt is assembled in three tiers, concatenated in this order:
  1. **Stable** — identity and persona; byte-identical across turns so the prompt prefix stays cache-hot.
  2. **Session** — slow-changing: user profile plus retrieved memory.
  3. **Volatile** — per-turn reminders, riding the tail.
- **Why the ordering is a cost decision, not a formatting one.** Providers cache on prefix; a cache hit costs a fraction of a fresh input token. Anything per-turn placed near the front invalidates the cached prefix every single turn and silently multiplies input cost. This matters most for exactly the agents nuvel targets: long stable personas plus heavy retrieved memory.
- The contract an agent author must preserve when adding instruction content: new persona text goes in the stable tier; anything derived from session state goes in the session tier; anything that changes every turn goes in the volatile tier. Never interpolate per-turn values into the stable tier.
- Graceful degradation: the session tier degrades rather than failing when memory sources are unavailable (`test_session_tier_degrades_when_sources_fail`).
- Note that this contract is pinned by `tests/test_prompt_tiers.py`, naming `test_full_prompt_stable_prefix_survives_volatile_change` and `test_stable_tier_ignores_volatile_state`, so a future edit that breaks it fails the suite.
- Cross-reference `adk-long-horizon-sessions` for how compaction interacts with the session tier.

- [ ] **Step 2: Patch `adk-callbacks-hitl`**

Add a short subsection noting that the shipped plugin chain already installs real callbacks the reader will encounter: `GuardrailsPlugin` provides `before_model_callback` (halt consumer), `after_model_callback` (no-progress) and `after_tool_callback` (repeated-failure); `exfil_guard` is a `before_tool_callback`; `CronIsolationPlugin` is a `before_tool_callback` active only during cron runs. Add the halt latch as a worked state-key example (a callback that writes a reason into session state and a `before_model_callback` that short-circuits while it is set). Link `adk-long-horizon-guardrails` and `adk-cron-isolation`.

- [ ] **Step 3: Patch `adk-agent-patterns`**

In the loop-pattern discussion, name halt guards as the runaway backstop: a `LoopAgent` needs a termination condition, and `NoProgressGuard` / `RepeatedFailureGuard` are the safety net when that condition fails to trigger. Link `adk-long-horizon-guardrails`.

- [ ] **Step 4: Patch `adk-tool-creation`**

Add a note that tools do not run unconditionally in a generated agent: the command-safety classifier can return `deny`/`ask` for shell-executing tools, `exfil_guard` can block a call whose arguments carry a secret, and during a cron run the headless policy may auto-deny the tool entirely. Tools should therefore surface denial as a structured, non-fatal result rather than assuming execution. Link `adk-long-horizon-guardrails` and `adk-cron-isolation`.

- [ ] **Step 5: Patch `adk-skill-creation` and `adk-skill-design-patterns`**

In each, add a sentence noting that the optional skill curator (`NUVEL_SKILL_CURATOR`, default off) can *propose* `SKILL.md` files from observed tool usage, that proposals land in `NUVEL_SKILL_PROPOSALS_DIR` and are meant for human review before becoming live skills, and that the conventions in these two skills are what a proposal should be judged against. Link `adk-memory-self-improvement`.

- [ ] **Step 6: Verify the four untouched skills need no change**

Read `adk-streaming`, `adk-task-delegation`, `adk-workflow-graphs`, `adk-composio-tool-router` and confirm no claim in them is contradicted by the new subsystems. If a claim *is* contradicted, fix it and note it in the commit message. Expected outcome: no changes needed.

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: ≥ 937 passed, 12 skipped.

- [ ] **Step 8: Commit**

```bash
git add nuvel/backends/adk/skills
git commit -m "docs(skills): cross-reference new subsystems from the existing ADK skills

Adds the 3-tier cache-stable prompt contract to adk-prompt-engineering (prompt
prefix stability is a cost decision, pinned by tests/test_prompt_tiers.py), and
wires guardrails/cron/curator cross-references into callbacks-hitl, agent-patterns,
tool-creation, skill-creation and skill-design-patterns."
```

---

### Task 9: Enforce the skill count, then open PR 1

**Files:**
- Modify: `tests/test_skills_integrity.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_skills_integrity.py`:

```python
@pytest.mark.parametrize("framework", sorted(EXPECTED_SKILL_COUNTS))
def test_skill_count_matches_expectation(framework: str) -> None:
    """A new skill must be registered here, so counts in docs cannot silently drift."""
    actual = len(_skill_dirs(framework))
    expected = EXPECTED_SKILL_COUNTS[framework]
    assert actual == expected, (
        f"{framework}: found {actual} skills, expected {expected}. "
        "If this is intentional, update EXPECTED_SKILL_COUNTS and every documented "
        "count (.claude/skills/nuvel/SKILL.md, CLAUDE.md, README.md)."
    )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_skills_integrity.py -k count -q`
Expected: FAIL for `adk` — `found 15 skills, expected 10` (the constant still says 10 from Task 1).

- [ ] **Step 3: Update the expectation to the true count**

In `tests/test_skills_integrity.py`, change:

```python
EXPECTED_SKILL_COUNTS = {"adk": 15, "claude_agent_sdk": 6, "anthropic_managed_agents": 5}
```

and delete the `# Updated by Task 9…` comment above it.

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest tests/test_skills_integrity.py -q`
Expected: all PASS.

- [ ] **Step 5: Verify the CLI agrees**

Run: `python -m nuvel.cli skills list | wc -l`
Expected: `15`.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: **≥ 950 passed, 12 skipped** (895 baseline + 42 reference/frontmatter for 21 skills, growing to 30+30 for 15 ADK skills, + 3 count tests). Record the exact number in the PR body.

- [ ] **Step 7: Commit and push**

```bash
git add tests/test_skills_integrity.py
git commit -m "test(skills): assert per-framework skill counts (15/6/5)

Adding a skill without updating the documented counts now fails the suite."
git push -u origin docs/v030-knowledge-layer
```

- [ ] **Step 8: Verify identity, then open PR 1**

```bash
git log --format='%an <%ae>' -12 | sort -u   # must be only: Folken2 <folkenai21@gmail.com>
gh api user --jq .login                       # must be: Folken2
```

Then:

```bash
gh pr create --base main --head docs/v030-knowledge-layer \
  --title "docs: five ADK knowledge skills for guardrails, cron isolation + org memory" \
  --body "$(cat <<'BODY'
## Why

Six feature PRs landed between v0.2.0 and 643994f (13,044 insertions, 102 files).
#48/#49 updated the knowledge layer in the same commit; #50/#51/#54/#55 did not,
leaving six subsystems with no skill coverage. nuvel ships its knowledge layer as a
product surface, so this was a product regression rather than a docs backlog.

## What

Five new ADK knowledge skills:

| Skill | Covers |
|---|---|
| `adk-long-horizon-guardrails` | Halt latch, no-progress, repeated-failure, argv-level command safety, exfil guard |
| `adk-cron-isolation` | Scoped secrets, headless tool policy, HITL-gated job creation |
| `adk-org-memory-retrieval` | Service-registry wiring, hybrid RRF, knowledge graph, relational recall, synthesis |
| `adk-memory-self-improvement` | Experimental: preload, consolidation, judge fork, sibling runs, skill curator |
| `adk-long-horizon-sessions` | Resumability, event compaction, and their interaction with the prompt tiers |

Plus:

- A substantial **3-tier cache-stable prompt** section in `adk-prompt-engineering` —
  prompt prefix stability is a cost decision, and it was entirely undocumented.
- Cross-references from `adk-callbacks-hitl`, `adk-agent-patterns`,
  `adk-tool-creation`, `adk-skill-creation`, `adk-skill-design-patterns`.
- `references/composio-patterns.md`, which `adk-composio-tool-router` promised and
  never shipped — the only dangling reference in the repo.
- `THIRD_PARTY.md` crediting `garrytan/gbrain` (MIT, © 2026 Garry Tan) for the
  org-memory retrieval designs.
- `tests/test_skills_integrity.py` — reference-existence, frontmatter validity, and
  per-framework skill counts, so this drift cannot recur silently.

## Verification

`python -m pytest tests/ -q` — no regression from the 895-passing baseline.

Design: `docs/superpowers/specs/2026-08-06-v030-skills-coverage-design.md`
Plan: `docs/superpowers/plans/2026-08-06-v030-knowledge-layer.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 9: Confirm PR authorship**

Run: `gh pr view --json author,title --jq '.author.login, .title'`
Expected: `Folken2` and the PR title. If the author is not Folken2, close the PR, run `gh auth switch --hostname github.com --user Folken2`, and re-open.

---

# PR 2 — Release prep

Start from a fresh branch off `main` **after PR 1 merges**, so PR 2 reviews cleanly.

### Task 10: Close the `.env.example` gap with a parity test

**Files:**
- Modify: `tests/test_skills_integrity.py`
- Modify: `nuvel/backends/adk/templates/.env.example`

**Interfaces:**
- Consumes: `REPO_ROOT`, `BACKENDS` from Task 1.
- Produces: `TEMPLATE_DIR`, `ENV_EXAMPLE`, `ENV_READ_PATTERNS`, `ENV_EXAMPLE_EXEMPT`.

- [ ] **Step 1: Create the branch**

```bash
git checkout main && git pull --ff-only origin main
git checkout -b docs/v030-release-prep
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_skills_integrity.py`:

```python
TEMPLATE_DIR = BACKENDS / "adk" / "templates"
ENV_EXAMPLE = TEMPLATE_DIR / ".env.example"

# Template code reads env vars three ways: os.getenv, os.environ.get/[], and via a
# module-level ENV_* name constant (e.g. ENV_PRELOAD = "NUVEL_MEMORY_PRELOAD").
ENV_READ_PATTERNS = (
    re.compile(r"""getenv\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""environ\.get\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""environ\[\s*["']([A-Z][A-Z0-9_]{2,})["']\s*\]"""),
    re.compile(r"""^\s*_?ENV_[A-Z0-9_]+\s*=\s*["']([A-Z][A-Z0-9_]{2,})["']""", re.M),
)

ENV_ENTRY_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", re.M)

# Read by template code but deliberately absent from .env.example. Each entry needs a
# reason — do not add to this set to silence a failure.
ENV_EXAMPLE_EXEMPT = {
    "RECORD": "test-only: golden-recording switch in tests/test_agent.py.tmpl",
    "HOST": "platform-provided; read only in run_adk.py's diagnostic dump",
    "TELEGRAM_BOT_TOKEN": "injected by the --with-telegram overlay's gateway env block",
}


def _env_vars_read_by_template_code() -> set[str]:
    found: set[str] = set()
    for path in TEMPLATE_DIR.rglob("*"):
        if not path.is_file() or path.name == ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in ENV_READ_PATTERNS:
            found |= set(pattern.findall(text))
    return found


def test_every_env_var_read_by_template_is_documented() -> None:
    """A knob the template reads must appear in .env.example, or be explicitly exempt."""
    documented = set(ENV_ENTRY_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    undocumented = sorted(
        _env_vars_read_by_template_code() - documented - set(ENV_EXAMPLE_EXEMPT)
    )
    assert not undocumented, (
        "These env vars are read by template code but absent from .env.example: "
        f"{undocumented}. Document them, or add to ENV_EXAMPLE_EXEMPT with a reason."
    )
```

Note: only the forward direction is asserted. A reverse check (documented but unread) was evaluated and rejected — legitimately unread entries include vars consumed by libraries rather than nuvel (`OPENROUTER_API_KEY`, `GOOGLE_API_KEY`) and dict-driven trace limits (`TRACE_MAX_*`), so the check would test its own exemption list rather than the code.

- [ ] **Step 3: Run it to verify it fails with the expected 18**

Run: `python -m pytest tests/test_skills_integrity.py -k env -q`
Expected: FAIL listing exactly these 18:
`CACHE_MAX_SIZE`, `CACHE_TTL_SECONDS`, `COMPACTION_INTERVAL`, `COMPACTION_OVERLAP`,
`COMPACTION_RETENTION`, `EXFIL_GUARD_STRICT`, `FORCE_COLOR`, `LLM_NUM_RETRIES`,
`LLM_REQUEST_TIMEOUT`, `NO_COLOR`, `NUVEL_SKILLS_DIR`, `NUVEL_SKILL_CURATOR`,
`NUVEL_SKILL_CURATOR_MIN_ERRORS`, `NUVEL_SKILL_CURATOR_MIN_EVENTS`,
`NUVEL_SKILL_CURATOR_MIN_TOOLS`, `NUVEL_SKILL_CURATOR_MODEL`,
`NUVEL_SKILL_PROPOSALS_DIR`, `RESUMABILITY`.

If the list differs, the template changed since this plan was written — reconcile before proceeding rather than editing the assertion.

- [ ] **Step 4: Add the 18 entries to `.env.example`**

Follow the file's existing style exactly: a `# ── Section ──` header, a `#` comment line explaining the knob, then the variable commented out with its default (`# VAR=default`). Add to existing sections where one fits, and create the three new sections shown below.

Into `# ── Resilience ───` (line 59):

```bash
# Optional: LLM retry + timeout behaviour
# LLM_NUM_RETRIES=3
# LLM_REQUEST_TIMEOUT=120     # seconds
```

Into `# ── Logging ───` (line 68):

```bash
# Optional: colour control (https://no-color.org). Set either to any value.
# NO_COLOR=1        # force plain output
# FORCE_COLOR=1     # force colour even when not a TTY
```

New section after `# ── Resilience ───`:

```bash
# ── Response cache ──────────────────────────────────────────────────

# Optional: in-memory response cache (CachePlugin)
# CACHE_MAX_SIZE=10           # max cached entries
# CACHE_TTL_SECONDS=300       # entry lifetime
```

New section after `# ── Long-Term Memory ───`:

```bash
# ── Long-horizon sessions ───────────────────────────────────────────

# Optional: resume an interrupted long run instead of restarting it.
# Set false when the deployment can't persist extra invocation state.
# RESUMABILITY=true

# Optional: roll old events into summaries so long sessions don't blow the
# context window. Overlap makes consecutive summaries share context.
# COMPACTION_INTERVAL=8       # compact every N user turns
# COMPACTION_OVERLAP=2        # turns shared between summaries
# COMPACTION_RETENTION=20     # most recent events kept verbatim
```

New section after `# ── Cron isolation hardening (opt-in) ───`:

```bash
# ── Guardrails ──────────────────────────────────────────────────────

# Optional: exfiltration guard. Strict (default) blocks a tool call whose
# arguments carry a secret; lax only flags it.
# EXFIL_GUARD_STRICT=1

# ── Skill curator (opt-in, default off) ─────────────────────────────

# Optional: let the agent propose its own SKILL.md files from observed tool
# usage. Proposals are for human review — they don't become live skills.
# NUVEL_SKILL_CURATOR=1
# NUVEL_SKILL_CURATOR_MIN_TOOLS=5      # distinct tools before proposing
# NUVEL_SKILL_CURATOR_MIN_EVENTS=12    # events before proposing
# NUVEL_SKILL_CURATOR_MIN_ERRORS=3     # repeated errors that trigger a proposal
# NUVEL_SKILL_CURATOR_MODEL=gemini-2.0-flash
# NUVEL_SKILL_PROPOSALS_DIR=            # override proposal output dir
# NUVEL_SKILLS_DIR=                     # override the agent's skills dir
```

- [ ] **Step 5: Run it to verify it passes**

Run: `python -m pytest tests/test_skills_integrity.py -q`
Expected: all PASS.

- [ ] **Step 6: Verify the scaffolder still renders `.env.example`**

The file contains `{{composio_env_block}}`, `{{gateway_env_block}}`, `{{acp_env_block}}` and `{{default_fast_model}}` placeholders — the additions must not disturb them.

Run: `python -m pytest tests/ -q`
Expected: no regression; scaffold tests still pass.

- [ ] **Step 7: Commit**

```bash
git add tests/test_skills_integrity.py nuvel/backends/adk/templates/.env.example
git commit -m "docs(env): document 18 undocumented template env vars, enforced by test

Adds a parity test asserting every env var the ADK template reads has a
.env.example entry, then closes the 18-var gap it found: the compaction/
resumability knobs and skill-curator family from PR #50, plus older cache, LLM
retry and colour vars. Three vars are exempt with recorded reasons."
```

---

### Task 11: Refresh the entry skill

**Files:**
- Modify: `.claude/skills/nuvel/SKILL.md:83` and the table at lines 92-103

- [ ] **Step 1: Update the count sentence**

Change line 83 from `nuvel` bundles 10 ADK knowledge skills. to `nuvel` bundles 15 ADK knowledge skills.

- [ ] **Step 2: Append five rows to the skills table**

After the `adk-composio-tool-router` row (line 103), matching the existing `| slug | Read when… |` style:

```markdown
| `adk-long-horizon-guardrails` | Halt guards, shell-command safety, exfil guard — running unattended or long |
| `adk-long-horizon-sessions` | Resumability + event compaction for runs that outlive one context window |
| `adk-cron-isolation` | Scheduled runs — scoped secrets, headless tool policy, HITL-gated creation |
| `adk-org-memory-retrieval` | Org-scoped memory — wiring, hybrid RRF retrieval, knowledge graph |
| `adk-memory-self-improvement` | EXPERIMENTAL — consolidation, judge forks, skill curator (cost caveats) |
```

- [ ] **Step 3: Add a long-horizon pointer**

After the table's trailing sentence (line 105), add two sentences: agents meant to run unattended or for a long time should read `adk-long-horizon-guardrails` and `adk-long-horizon-sessions` before deploy, because both ship default-on behaviour (halt guards, resumability, compaction) that changes how the agent behaves under load. Keep it to two sentences — this file is loaded eagerly.

- [ ] **Step 4: Verify counts agree**

Run: `python -m pytest tests/test_skills_integrity.py -k count -q && python -m nuvel.cli skills list | wc -l`
Expected: PASS and `15`.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/nuvel/SKILL.md
git commit -m "docs(skill): refresh entry skill for the five new ADK skills (10 -> 15)"
```

---

### Task 12: Fix `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md:38`, `CLAUDE.md:42-49`

- [ ] **Step 1: Fix the skill counts**

Line 38 currently reads `knowledge skills bundled with that framework (8 / 6 / 5 respectively)`. Change `8 / 6 / 5` to `15 / 6 / 5`. This was wrong before this work started — ADK had 10, not 8.

- [ ] **Step 2: Document the third duplicated chain**

The file already warns about two plugin chains (lines 44-49). Add an equivalent warning immediately after that section, because the same trap now exists a third time:

```markdown
### A third duplicated chain — guardrails

Same trap as the plugin chains above, one level deeper:

- `nuvel/guardrails/` — halt + command-safety guards for the **meta-agent**.
- `nuvel/backends/adk/templates/{{agent_package}}/guardrails/` — the identical chain
  *copied into every generated ADK agent*.

`nuvel/memory/` and `templates/{{agent_package}}/memory/` are duplicated the same way,
though not identically: the template copy adds the self-improvement layer
(consolidation, judge fork, sibling runner, skill curator) that the meta-agent doesn't
run. Editing one does not affect the other — check which side you're on.
```

- [ ] **Step 3: Note the new subsystems in the architecture section**

Add brief entries so a future reader finds them: guardrails (`GuardrailsPlugin`, always wired), cron isolation (`CronIsolationPlugin`, inert outside cron runs), and org memory (`nuvel-org-memory://` scheme registered with ADK's service registry). One line each, in the existing prose style.

- [ ] **Step 4: Verify no stale counts remain**

Run: `grep -nE '\b8 / 6 / 5\b|bundles 10 ADK|10 ADK knowledge' CLAUDE.md README.md .claude/skills/nuvel/SKILL.md || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): correct skill counts and document the guardrails/memory duplication

Counts said 8/6/5; ADK actually had 10 and now has 15. Also records that
guardrails/ and memory/ are duplicated between the meta-agent and the generated-agent
templates, the same trap CLAUDE.md already flags for the two plugin chains."
```

---

### Task 13: README pointer

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a configuration pointer**

Add a short **Configuration** subsection stating that every environment variable a generated agent reads is documented inline in `nuvel/backends/adk/templates/.env.example` (which is copied into each scaffolded agent as `.env.example`), grouped by subsystem. Deliberately do **not** reproduce the variables — duplicating 79 entries would create a second surface to drift, which is what `tests/test_skills_integrity.py` now guards against.

- [ ] **Step 2: Update the skills mention**

Find the existing skill-count reference in `README.md` and update it to 15 ADK skills, listing the five new slugs in one line.

- [ ] **Step 3: Verify**

Run: `grep -nE 'NUVEL_[A-Z_]+=' README.md || echo "no env table — correct"`
Expected: `no env table — correct`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): point at .env.example for configuration; note the five new skills"
```

---

### Task 14: `CHANGELOG.md`

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Gather the real history**

```bash
git log --oneline v0.1.0..v0.1.1
git log --oneline v0.1.1..v0.2.0
git log --oneline v0.2.0..main
```

- [ ] **Step 2: Write `CHANGELOG.md`**

Keep-a-Changelog format with a header noting the project follows Semantic Versioning. Backfill `## [0.1.0]`, `## [0.1.1]`, `## [0.2.0]` from the log above at summary granularity — do not fabricate detail. Then a full `## [0.3.0] - 2026-08-06` entry with `### Added` / `### Changed` / `### Fixed`, covering:

- **Added:** `--with-acp` (ACP adapter + local CLI, #48) with editor `mcpServers` and fs bridge (#49); long-horizon guardrails, resumability, event compaction and memory self-improvement (#50); cron isolation — scoped secrets, headless policy, HITL-gated creation (#51); OrgMemoryService runner wiring via ADK's service registry (#54); hybrid RRF retrieval, knowledge graph and relational recall (#55); five new ADK knowledge skills; `THIRD_PARTY.md`; `tests/test_skills_integrity.py`.
- **Changed:** the 3-tier cache-stable prompt contract documented in `adk-prompt-engineering`; 18 previously undocumented env vars added to `.env.example`; skill counts corrected in `CLAUDE.md` (they claimed 8, ADK had 10).
- **Fixed:** the dangling `references/composio-patterns.md` pointer in `adk-composio-tool-router`.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md, backfilled to 0.1.0 with a full 0.3.0 entry"
```

---

### Task 15: Version bump and PR 2

**Files:**
- Modify: `pyproject.toml:7`

- [ ] **Step 1: Bump the version**

Change `version = "0.2.0"` to `version = "0.3.0"`. SemVer MINOR: six PRs of backward-compatible features, nothing removed.

- [ ] **Step 2: Verify the release workflow's guard will pass**

The workflow fails the build if the tag doesn't match `pyproject.toml`. Confirm the value the guard will read:

```bash
python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])"
```
Expected: `0.3.0` — this must equal the tag minus its leading `v`.

- [ ] **Step 3: Run the full suite one final time**

Run: `python -m pytest tests/ -q`
Expected: ≥ 950 passed, 12 skipped, 0 failed.

- [ ] **Step 4: Commit and push**

```bash
git add pyproject.toml
git commit -m "chore: bump version to 0.3.0"
git push -u origin docs/v030-release-prep
```

- [ ] **Step 5: Verify identity and open PR 2**

```bash
git log --format='%an <%ae>' -8 | sort -u   # only Folken2 <folkenai21@gmail.com>
gh api user --jq .login                      # Folken2
```

```bash
gh pr create --base main --head docs/v030-release-prep \
  --title "docs: v0.3.0 release prep" \
  --body "$(cat <<'BODY'
## What

Release prep for v0.3.0, following the knowledge-skills PR.

- **`.env.example`** — documents 18 env vars the template reads but never listed:
  the compaction/resumability knobs and the whole skill-curator family from #50,
  plus older cache, LLM-retry and colour vars. Enforced by a new parity test; three
  vars are exempt with recorded reasons (test-only, platform-provided, overlay-injected).
- **`.claude/skills/nuvel/SKILL.md`** — 10 → 15 skills, with the five new rows.
- **`CLAUDE.md`** — corrects the skill counts (claimed 8/6/5, ADK actually had 10)
  and records that `guardrails/` and `memory/` are duplicated between the meta-agent
  and the generated-agent templates — the same trap already flagged for plugins.
- **`README.md`** — points at `.env.example` as the configuration reference. No env
  table on purpose: duplicating 79 entries would just create a second surface to drift.
- **`CHANGELOG.md`** — new, backfilled to 0.1.0, with a full 0.3.0 entry.
- **`pyproject.toml`** — 0.2.0 → 0.3.0.

## Why 0.3.0

SemVer MINOR: six PRs of backward-compatible new features (13,044 insertions vs 130
deletions), nothing removed. 0.2.1 would misrepresent a feature release as a bugfix.

## Verification

`python -m pytest tests/ -q` green. `pyproject.toml` version matches the planned tag,
which the release workflow's guard checks.

**Not included:** the `v0.3.0` tag. Pushing it triggers the PyPI publish and is a
separate, explicitly confirmed step.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

- [ ] **Step 6: Confirm PR authorship**

Run: `gh pr view --json author --jq .author.login`
Expected: `Folken2`.

---

### Task 16: Tag the release — HUMAN GATE

**Do not perform any step in this task without explicit human confirmation.**

- [ ] **Step 1: Confirm preconditions**

```bash
git checkout main && git pull --ff-only origin main
git log --oneline -3
python -c "import tomllib,pathlib;print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])"
python -m pytest tests/ -q
```
Both PRs must be merged, the version must read `0.3.0`, and the suite must be green.

- [ ] **Step 2: Ask the human, in these terms**

State plainly: pushing `v0.3.0` runs `.github/workflows/release.yml`, which builds the distribution, **publishes `nuvel-cli` 0.3.0 to PyPI**, and creates a GitHub release. PyPI permanently refuses re-uploads of a version, so a mistake cannot be fixed by re-tagging — only yanked and superseded by `0.3.1`. Ask for explicit confirmation to proceed. **Stop here until it is given.**

- [ ] **Step 3: Tag and push (only after confirmation)**

```bash
git tag -a v0.3.0 -m "v0.3.0 — ACP adapter, long-horizon guardrails, cron isolation, org memory + knowledge skills"
git push origin v0.3.0
```

- [ ] **Step 4: Watch the release run**

```bash
gh run watch "$(gh run list --workflow=release.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```
Expected: `build` → `publish-pypi` → `github-release` all green.

- [ ] **Step 5: Verify the published artifact**

```bash
gh release view v0.3.0 --json name,assets --jq '.name, (.assets|length)'
curl -s https://pypi.org/pypi/nuvel-cli/json | python -c "import json,sys;print(json.load(sys.stdin)['info']['version'])"
```
Expected: `v0.3.0`, at least 2 assets (sdist + wheel), and PyPI reporting `0.3.0`.

If `publish-pypi` fails, do **not** re-tag. Diagnose, fix on a branch, and release `0.3.1`.

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: five new skills → Tasks 3-7; the 3-tier prompt section and the 10-skill audit → Task 8; the missing composio reference → Task 1; `THIRD_PARTY.md` → Task 2; the integrity test's four assertions → Tasks 1 (references, frontmatter), 9 (counts), 10 (env parity); `.env.example` → Task 10; entry skill → Task 11; `CLAUDE.md` → Task 12; README → Task 13; `CHANGELOG.md` → Task 14; version bump → Task 15; tagging → Task 16.

**Two deliberate deviations from the spec**, both discovered while validating the test code against the repo:

1. **The env gap is 18, not 12.** The spec's figure came from a `getenv(`-only scan. Template code also uses `os.environ.get` and indirects through `ENV_*` name constants (e.g. `ENV_PRELOAD = "NUVEL_MEMORY_PRELOAD"`), so the validated four-pattern scanner finds the whole `NUVEL_SKILL_CURATOR*` family too. Three of the 21 raw hits are legitimately exempt (`RECORD` test-only, `HOST` platform-provided, `TELEGRAM_BOT_TOKEN` overlay-injected), giving 18.
2. **The reverse parity check is dropped.** The spec called for both directions. Validation showed 9 immediate false positives — vars consumed by libraries rather than nuvel (`OPENROUTER_API_KEY`, `GOOGLE_API_KEY`) and dict-driven trace limits (`TRACE_MAX_*`). A check needing 9 exemptions on day one tests its exemption list, not the code. The forward check is the one that catches real drift.

**Placeholder scan:** no TBD/TODO. Every code step carries complete code; every prose step names exact `##` headings, the facts to cover, and the source file to distil from.

**Type consistency:** `_skill_dirs`, `_all_skill_dirs`, `_frontmatter`, `FRAMEWORK_DIRS`, `EXPECTED_SKILL_COUNTS`, `REFERENCE_RE` are defined in Task 1 and reused under those exact names in Tasks 9 and 10. `EXPECTED_SKILL_COUNTS` is deliberately seeded at `adk: 10` in Task 1 so Task 9's test fails first, then updated to `15`. `ENV_EXAMPLE_EXEMPT` is a dict (name → reason), so `set(ENV_EXAMPLE_EXEMPT)` is used for set arithmetic.
