# Eval Harness v1 — Implementation Plan

**Spec:** [`2026-05-20-eval-harness-v1-design.md`](../specs/2026-05-20-eval-harness-v1-design.md)
**Date:** 2026-05-20
**Owner:** @Folken2

## Context

Build the online trace scorer per the v1 spec. Heuristics-first, judge-on-pass, scored output as `scored.jsonl` siblings, `nuvel eval` CLI, dashboard integration, drift detection. No fixtures, no CI gate.

This plan sequences the work in **six phases**, each independently testable and shippable. Each phase ends with a concrete verification step. Estimated total: ~6–8 commits across one branch (`feature/eval-harness`).

## Pre-flight

**Branch:** `feature/eval-harness` off current `main` (post merge of #33 / #34).

**Dependencies to add** (`requirements.txt`): none. `litellm` and `pyyaml` are already top-level deps.

**Open question to resolve before Phase 3:**
- Does `litellm.acompletion` work with `EVAL_JUDGE_MODEL=openrouter/moonshotai/kimi-k2.5` against an `OPENROUTER_API_KEY`-only environment? Verify with a one-line script during Phase 3 setup; if not, fall back to direct provider id (`anthropic/claude-haiku-4-5`) and document it.

---

## Phase 1 — Foundation: schema + module scaffold

**Goal:** Wire the package layout, define `ScoredRun`, prove nothing breaks.

**Files:**
- `nuvel/eval/__init__.py` — public exports: `ScoredRun`, `score_run`, `SCORER_VERSION`
- `nuvel/eval/schema.py` — `ScoredRun`, `JudgeResult`, `Flag` (enum), `SCORER_VERSION = "1.0"` constant; pure dataclasses, no I/O
- `tests/test_eval_schema.py` — round-trip serialize/deserialize, version constant present

**Verification:**
```bash
python -m pytest tests/test_eval_schema.py -v
python -c "from nuvel.eval import ScoredRun, SCORER_VERSION; print(SCORER_VERSION)"
```

**Commit:** `feat(eval): schema scaffold for ScoredRun + SCORER_VERSION`

---

## Phase 2 — Heuristics: deterministic flag rules

**Goal:** Pure-function rules that consume a `Run` (+ its raw events) and emit `list[Flag]` + component penalties.

**Design decision:** `Run` (from `nuvel/traces_cli.py`) only carries the summary fields. Three heuristics need raw events (`tool_error`, `tool_loop`, `no_assistant_output`). Options:
- (A) Re-read JSONL on demand inside heuristics. Simple, slow if scanning many runs.
- (B) Extend `Run` with a lazy `events()` method. Cleanest. **Chosen.**

**Files:**
- `nuvel/traces_cli.py` — add `Run.events(self) -> list[dict]` that lazy-loads + caches the run's events from its source file. Existing call sites unaffected.
- `nuvel/eval/heuristics.py` — one pure function per flag rule, plus `apply_heuristics(run, baseline_stats) -> tuple[list[Flag], dict[component, float]]` orchestrator. `baseline_stats` is the precomputed p95/p99 dict per agent.
- `nuvel/eval/stats.py` — `compute_baseline_stats(runs: list[Run]) -> dict[agent, BaselineStats]` (rolling p95/p99 of cost, latency, tokens, turns over last 100 same-agent runs).
- `tests/test_eval_heuristics.py` — fixture `Run` objects per flag; assert exact penalty math; assert percentile rules.

**Notes:**
- `tool_error` detection: events with `event == "tool_response"` and `error` field set. Verify exact ADK shape by inspecting one outlook-king trace file before coding.
- `excessive_turns` baseline: `num_turns > 20` as spec says, BUT if the run is tagged as an explicit multi-turn session, raise threshold to 40. Defer until we see real data; v1 uses flat 20.
- `tool_loop` detection: sliding-window check on the sequence of `tool_request` event names.

**Verification:**
```bash
python -m pytest tests/test_eval_heuristics.py tests/test_eval_stats.py -v
# Sanity-check on real data: should print flags, not raise.
python -c "
from nuvel.traces_cli import _discover_trace_dirs, _iter_trace_files, load_runs
from nuvel.eval.heuristics import apply_heuristics
from nuvel.eval.stats import compute_baseline_stats
runs = list(load_runs(_iter_trace_files(_discover_trace_dirs())))
stats = compute_baseline_stats(runs)
for r in runs[:5]:
    print(r.trace_id, apply_heuristics(r, stats))
"
```

**Commit:** `feat(eval): deterministic heuristics + baseline stats`

---

## Phase 3 — Judge: single LLM call, structured output

**Goal:** One async function: `judge_run(run, rubric) -> JudgeResult`. Uses `litellm.acompletion`, returns parsed JSON, retries once, captures cost.

**Files:**
- `nuvel/eval/judge.py`:
  - `JUDGE_PROMPT_TEMPLATE` constant (assembled in code, not Jinja).
  - `async def judge_run(run: Run, rubric: Rubric, *, model: str | None = None) -> JudgeResult` — resolves model via the spec's priority chain; calls `litellm.acompletion`; parses JSON; retries once on `json.JSONDecodeError` or `litellm` transient errors; returns `JudgeResult(success, quality, notes, cost_usd, model, error)`.
- `nuvel/eval/rubric.py`:
  - `Rubric` dataclass: `version`, `weights`, `judge_model: str | None`, `extra_criteria: str`.
  - `load_rubric(agent: str) -> Rubric` — reads `generated-agents/<agent>/evals/rubric.yaml` if present, otherwise returns default.
  - `DEFAULT_RUBRIC` constant.
- `tests/test_eval_judge.py` — mock `litellm.acompletion`; verify prompt assembly, retry, error fallback, cost extraction.
- `tests/test_eval_rubric.py` — default rubric, agent override loading, malformed YAML failure.

**Verification:**
```bash
python -m pytest tests/test_eval_judge.py tests/test_eval_rubric.py -v
# Real call (gated on OPENROUTER_API_KEY presence — skip if missing):
python -c "
import asyncio
from nuvel.eval.judge import judge_run
from nuvel.eval.rubric import DEFAULT_RUBRIC
from nuvel.traces_cli import load_runs, _discover_trace_dirs, _iter_trace_files
runs = list(load_runs(_iter_trace_files(_discover_trace_dirs())))
r = next(r for r in runs if r.user_input)
print(asyncio.run(judge_run(r, DEFAULT_RUBRIC)))
"
```

**Commit:** `feat(eval): LLM judge with rubric loader + retry`

---

## Phase 4 — Scorer orchestrator + writer

**Goal:** Compose Phases 2+3 into `score_run(run, ...) -> ScoredRun`, plus a batch driver that handles idempotency, concurrency, cost budget, and `scored.jsonl` writes.

**Files:**
- `nuvel/eval/scorer.py`:
  - `async def score_run(run, *, rubric, baseline_stats, judge_client, force=False) -> ScoredRun` — heuristics → judge (or skip) → component math → overall score.
  - `class ScoreSession` — batch orchestrator. Constructor takes trace dirs, rubric resolver, model, max-cost. Method `async def run(self) -> ScoreReport`. Owns:
    - Loading existing `scored.jsonl` per dir → skip-set of `(trace_id, scorer_version)` already scored.
    - Concurrency: `asyncio.Semaphore(5)`.
    - Cost budget: tally `judge.cost_usd` across runs; once exceeded, stop launching new judges (heuristics-only thereafter), log warn.
    - Per-dir output: append-only writes to `scored.jsonl` siblings.
- `nuvel/eval/writer.py` — `append_scored(path, scored_run)` + `load_scored_index(path) -> dict[trace_id, ScoredRun]`. Thin, only-job module so test surface is small.
- `tests/test_eval_scorer.py` — orchestration paths: heuristic-floor skip, full judge path, idempotent skip, force-rescore, cost-budget early exit, version-bump rescore.
- `tests/test_eval_writer.py` — append + load round-trip; malformed line tolerance (skip + warn).

**Verification:**
```bash
python -m pytest tests/test_eval_scorer.py tests/test_eval_writer.py -v
# End-to-end smoke against real traces:
python -c "
import asyncio
from nuvel.eval.scorer import ScoreSession
asyncio.run(ScoreSession(max_cost_usd=0.10).run())
"
ls generated-agents/outlook-king/traces/scored.jsonl
```

**Commit:** `feat(eval): scorer orchestrator with idempotent writes`

---

## Phase 5 — CLI: `nuvel eval {score,report,worst,drift}`

**Goal:** User-facing commands, wired into `nuvel/cli.py` dispatch alongside existing subcommands.

**Files:**
- `nuvel/eval/cli.py` — argparse subparsers for `score | report | worst | drift`. Each delegates to a function in `scorer.py` / `report.py`. Output is human-readable text tables (use existing table-rendering helpers from `nuvel/traces_cli.py` if present, else simple aligned columns).
- `nuvel/eval/report.py` — `def report(runs, scored_index) -> str` (mean overall, per-component, top flags per agent) and `def worst(n, ...) -> str`.
- `nuvel/eval/drift.py` — `def detect_drift(scored: list[ScoredRun], window_days, threshold) -> list[DriftReport]`. Pure function over scored history.
- `nuvel/cli.py` — register `eval` subcommand pointing at `nuvel.eval.cli.main`.
- `tests/test_eval_cli.py` — argparse paths, `--dry-run` writes nothing, idempotency check via second invocation.
- `tests/test_eval_drift.py` — synthetic scored history; threshold crossing edge cases.

**Verification:**
```bash
python -m pytest tests/test_eval_cli.py tests/test_eval_drift.py -v
nuvel eval score --dry-run
nuvel eval score --max-cost-usd 0.50
nuvel eval report --since 7d
nuvel eval worst --n 5
nuvel eval drift --window 7d
```

**Commit:** `feat(eval): nuvel eval CLI — score, report, worst, drift`

---

## Phase 6 — Dashboard integration: Score column + flag badges

**Goal:** Existing dashboard run list gains `Score` and `Flags` columns; sortable by score; default sort = worst-first when scored data exists in the visible window.

**Files:**
- `nuvel/dashboard/loader.py` — side-load `scored.jsonl` for each scanned `traces/*.jsonl` dir; join on `trace_id`. `Run`-equivalent payloads gain optional `score` / `flags` fields.
- `nuvel/dashboard/templates/<the runs template>.html` — add `Score` and `Flags` columns. Coalesce missing scores to `—`.
- `nuvel/dashboard/static/<css>` — flag badge styles. Keep monochrome to match existing aesthetic.
- `tests/test_dashboard_loader.py` — augment existing tests (or add new) with `scored.jsonl` alongside; assert join behavior, missing-score coalesce.

**Verification:**
```bash
python -m pytest tests/test_dashboard_loader.py -v
nuvel dashboard --port 8765  # browse, confirm Score + Flags render
```

**Commit:** `feat(dashboard): surface eval scores + flag badges in run list`

---

## Phase 7 — Docs + README touch

**Goal:** New env var + CLI documented; no surprises for users.

**Files:**
- `docs/reference/env-vars.md` — add `EVAL_JUDGE_MODEL` row (default = `DEFAULT_FAST_MODEL` from `_defaults.py`).
- `docs/reference/cli.md` — add `nuvel eval` section mirroring the structure of the existing `nuvel traces` section.
- `README.md` — one-paragraph mention under whatever the existing "what nuvel does" section is.

**Verification:** read-through; mkdocs build clean if used.

**Commit:** `docs(eval): document nuvel eval CLI and EVAL_JUDGE_MODEL`

---

## End-to-end verification (post all phases)

```bash
# 1. Fresh repo state
git checkout feature/eval-harness && python -m pytest tests/ -v

# 2. Score real production traces with a small budget
EVAL_JUDGE_MODEL=openrouter/moonshotai/kimi-k2.5 nuvel eval score --max-cost-usd 0.20

# 3. Inspect outputs
ls -la generated-agents/*/traces/scored.jsonl
nuvel eval report
nuvel eval worst --n 10

# 4. Dashboard
nuvel dashboard
# → confirm Score column populated, sort works, flag badges render

# 5. Idempotency
nuvel eval score --max-cost-usd 0.20
# → second run should report ~0 new scores, $0 spent

# 6. Force-rescore
nuvel eval score --force --max-cost-usd 0.20
# → all runs rescored; budget consumed
```

## Critical files (modified or created)

```
nuvel/eval/
  __init__.py         # NEW
  schema.py           # NEW
  heuristics.py       # NEW
  stats.py            # NEW
  rubric.py           # NEW
  judge.py            # NEW
  scorer.py           # NEW
  writer.py           # NEW
  drift.py            # NEW
  report.py           # NEW
  cli.py              # NEW
nuvel/traces_cli.py   # MODIFIED — add Run.events() lazy loader
nuvel/cli.py          # MODIFIED — register `eval` subcommand
nuvel/dashboard/loader.py            # MODIFIED — join scored.jsonl
nuvel/dashboard/templates/*.html     # MODIFIED — Score + Flags columns
docs/reference/env-vars.md           # MODIFIED — EVAL_JUDGE_MODEL row
docs/reference/cli.md                # MODIFIED — nuvel eval section
README.md                            # MODIFIED — one-paragraph mention
tests/test_eval_*.py  # NEW — schema/heuristics/judge/scorer/writer/cli/drift
```

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `litellm.acompletion` semantics differ between providers (cost field absence, response shape) | Wrap in `judge.py` with a thin adapter; cost extraction is one function, easy to special-case |
| `Run.events()` lazy loader hits the same file 7× in a busy heuristic pass | Cache per-`Run` instance; baseline-stats pass loads all once into memory anyway |
| Real traces are messier than expected (missing fields, malformed JSON lines) | `writer.py`/loaders skip + warn rather than crash; phase 2 sanity-check script catches surprises before coding the judge |
| Kimi K2.5 returns inconsistent JSON | Retry once; on second failure, mark `judge.error` and use heuristics-only score (already designed in) |
| Score column makes dashboard cluttered | Coalesce missing scores to a thin `—`; defer drift indicator to v1.1 |
