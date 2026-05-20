# Eval Harness v1 — Design

**Status:** Draft — pending approval
**Date:** 2026-05-20
**Owner:** @Folken2

## Context

Nuvel has observability (traces JSONL per agent, normalized through `Run`, surfaced in `nuvel traces` and the dashboard) but no grading layer. "The agent is getting better" is a vibe, not a measurement. Without scoring, regressions ship silently, prompt/model changes can't be A/B-judged, and the dashboard's "worst runs" view is unsortable beyond raw latency/cost.

This spec defines **v1** of `nuvel eval` — an **online trace scorer** that consumes existing traces, applies cheap heuristics plus an LLM-judge, and writes scored output back alongside traces. No test fixtures, no replay, no CI gate. Pure post-hoc scoring over real traffic.

The loop this closes: `traces → scored.jsonl → dashboard sort/filter → triage worst runs → fix`. Regression *alerting* (drift detection across time windows) is in scope for v1; alert *delivery* (Slack/email) is deferred to v1.1.

## Goals

1. Score every historical run in `./traces` and `generated-agents/*/traces` with low per-run cost (median <$0.0005).
2. **Heuristics-first**: deterministic rules run on every run; LLM-judge runs only when heuristics don't already condemn the run.
3. Idempotent — never re-score a `trace_id` unless `--force` or scorer version bumped.
4. Per-agent rubric override (optional YAML); single default rubric ships in v1.
5. Versioned scorer output — schema and rubric changes are detectable, rescorable.
6. CLI surface mirrors `nuvel traces` ergonomics. Dashboard gains a "Score" column.

## Non-Goals (v1)

- Golden-set / regression CI gate (separate harness, see "Future").
- Replay with perturbed config (separate harness).
- Alert delivery (Slack/email/webhook) — v1 surfaces drift in CLI/dashboard only.
- Multi-judge consensus or ensemble.
- Human-labeled training set for judge calibration.
- Storing scored output in Postgres (stays JSONL; revisit when `OrgMemoryService` lands and a shared DB is justified).
- Scoring intermediate LLM calls — v1 scores at the `Run` level only.

## Approaches Considered

| # | Approach | Verdict |
|---|---|---|
| A | Pure heuristics (no LLM judge) | Rejected — misses semantic quality; "did the agent actually answer the question" needs a judge |
| B | Pure LLM judge over every run | Rejected — wasteful; many failures are unambiguous from heuristics alone (tool error, no response) and shouldn't burn a judge call |
| C | **Heuristics-first, judge-on-pass** | **Chosen** — cheap signals first, semantic judge only when needed; bounded cost, fast triage path |
| D | Inline scoring during agent runtime (callback) | Deferred — couples eval to agent lifecycle, slows runs, harder to rescore historical data. Offline is the right v1 surface |

## Architecture

```
nuvel/eval/
  __init__.py
  schema.py        # ScoredRun dataclass + ScorerVersion constant
  heuristics.py    # deterministic rules → list[Flag]
  judge.py         # LLM-judge call + prompt; returns JudgeResult
  rubric.py        # default rubric YAML + per-agent override loader
  scorer.py        # orchestrator: Run → ScoredRun
  drift.py         # rolling-window mean comparison → DriftReport
  cli.py           # `nuvel eval {score,report,worst,drift}` commands
```

Module boundaries:

- **`scorer.py`** orchestrates. Doesn't know rubric YAML format or judge prompt text — those live in their modules.
- **`heuristics.py`** is pure functions over a `Run`. No I/O, no LLM. Deterministic and unit-testable.
- **`judge.py`** owns one LLM call + prompt assembly + JSON parsing + retry. Knows nothing about heuristics.
- **`schema.py`** defines the wire format. Bumping schema version is a deliberate edit here.

Trace input is the existing `Run` summary from `nuvel/traces_cli.py` — eval doesn't re-parse JSONL.

## Data Model

`scored.jsonl` lives next to each `traces/<file>.jsonl` (one scored file per source agent). One JSON object per scored `Run`:

```json
{
  "trace_id": "abc123",
  "agent": "outlook-king",
  "scored_at": "2026-05-20T14:00:00+00:00",
  "scorer_version": "1.0",
  "rubric_version": "default-1.0",
  "overall": 0.78,
  "components": {
    "success": 1.0,
    "quality": 0.7,
    "efficiency": 0.85,
    "reliability": 1.0
  },
  "flags": ["latency_outlier"],
  "judge": {
    "model": "claude-haiku-4-5",
    "cost_usd": 0.00041,
    "notes": "Answered the user's question correctly but with one redundant tool call."
  },
  "skipped_judge": false
}
```

`overall = weighted mean of components`. Default weights: `success=0.4, quality=0.3, efficiency=0.15, reliability=0.15`. Tunable per rubric.

### Heuristic flags (deterministic, cheap)

| Flag | Trigger | Penalty |
|---|---|---|
| `tool_error` | Any `tool_response` with error status in trace events | `reliability = 0` |
| `no_assistant_output` | `run_end` without a final assistant message | `success = 0`, skip judge |
| `excessive_turns` | `num_turns > 20` | `efficiency -= 0.3` |
| `cost_outlier` | Cost > p95 of last 100 runs of same agent | `efficiency -= 0.2` |
| `latency_outlier` | `duration_ms > p95` of same agent's last 100 | `efficiency -= 0.1` |
| `tool_loop` | Same tool name called ≥5× consecutively | `reliability -= 0.5`, `efficiency -= 0.2` |
| `token_bloat` | `total_tokens > p99` of same agent's last 100 | `efficiency -= 0.2` |

Heuristic-only floor: if `success = 0` from `no_assistant_output`, **skip the judge entirely** (cheap exit). Otherwise heuristics adjust components and the judge fills in `quality` + ratifies `success`.

### LLM Judge

Single Haiku 4.5 call per qualifying run. Prompt assembles `user_input`, the assistant's final response, and a compact tool-call summary. Asks for structured JSON:

```json
{
  "did_solve": 0.0,
  "quality": 0.0,
  "efficiency_note": "...",
  "notes": "one sentence"
}
```

Judge sets `success` (overriding heuristic 1.0 default) and `quality`. `efficiency_note` informs human readers, doesn't directly score.

**Model selection.** Default model resolves in this order: per-agent `rubric.yaml` `judge.model` → `EVAL_JUDGE_MODEL` env var → `DEFAULT_FAST_MODEL` from `nuvel/_defaults.py` (currently `openrouter/moonshotai/kimi-k2.5`). The env var is the operator knob for swapping models across all agents without touching rubric files; the rubric override is the per-agent escape hatch. All calls go through `litellm` (already a top-level dep), so any provider-prefixed id works.

Retry once on JSON parse failure. On second failure, write `judge.error` and skip judge scoring (heuristics-only `overall`).

### Rubric

Default rubric ships in code (`rubric.py`). Per-agent override via optional `generated-agents/<agent>/evals/rubric.yaml`:

```yaml
version: "outlook-king-1.0"
weights:
  success: 0.5
  quality: 0.3
  efficiency: 0.1
  reliability: 0.1
judge:
  model: claude-haiku-4-5
  extra_criteria: |
    - Did the assistant respect the user's email tone preferences (formal/casual)?
```

`extra_criteria` is appended to the judge prompt verbatim. No template language — keep it boring.

## CLI Surface

```
nuvel eval score [--since 7d] [--agent X] [--force] [--dry-run] [--max-cost-usd 1.00]
nuvel eval report [--since 7d] [--agent X]
nuvel eval worst [--n 10] [--agent X]
nuvel eval drift [--window 7d] [--agent X]
```

- `score` — scans traces, writes `scored.jsonl`. Idempotent (skip already-scored unless `--force` or `scorer_version` differs). Respects `--max-cost-usd` budget; aborts early when exceeded.
- `report` — summary table per agent: count, mean overall, mean per component, top flags.
- `worst` — N worst runs by overall score, with judge notes inline. Triage entry point.
- `drift` — rolling-window mean comparison. v1 prints a table; v1.1 wires alerts.

## Drift Detection (v1)

Per agent, compute:
- `current_mean = mean(overall) for last 7d`
- `baseline_mean = mean(overall) for the 7d before that`
- `delta = current_mean - baseline_mean`

CLI flags drift when `|delta| > 0.1`. v1 output is the table; v1.1 emits to a configurable webhook. The threshold is a flag (`--threshold 0.1`).

## Dashboard Integration

`nuvel/dashboard/loader.py` already builds `Run` lists. Side-load `scored.jsonl` for each trace file in the same dir; join on `trace_id`. Add columns:
- `Score` (overall, sortable)
- `Flags` (badge per flag)
- Default sort: when `--since` window has scored data, sort ascending by score (worst-first triage).

No new dashboard routes — augments existing list view. Drift indicator (▲/▼ per agent header) deferred to v1.1.

## Cost Budgeting

- Per-run judge cost ≈ $0.0005 (Haiku, ~2k input / ~150 output tokens).
- Heuristic-skip rate target: 30–50% (no judge needed).
- Default `--max-cost-usd 1.00` per `nuvel eval score` invocation (≈2000 runs).
- Concurrency: semaphore of 5 simultaneous judge calls. Rate-limit-safe for Anthropic.

Cost recorded per scored run in `judge.cost_usd`. `nuvel eval report` totals it.

## Error Handling

| Condition | Behavior |
|---|---|
| Trace file unreadable | Skip file, warn-log, continue |
| `Run` missing `user_input` | Heuristics-only score, flag `incomplete_trace`, skip judge |
| Judge HTTP error | Retry once with backoff; on second failure write `judge.error`, no judge score |
| Judge JSON parse failure | Same as HTTP error |
| `--max-cost-usd` exceeded mid-run | Stop launching new judges; write partial scored output; exit code 0 with warning |
| Rubric YAML invalid | Fail fast at command start; do not partially apply |

## Testing

- **Unit (`heuristics.py`):** fixture `Run` objects covering every flag trigger; assert exact component penalties.
- **Unit (`judge.py`):** mock LLM client; verify prompt assembly, JSON parsing, retry path, error path.
- **Unit (`scorer.py`):** orchestration — heuristics-skip path, full judge path, force-rescore path, version-bump rescore path.
- **Unit (`drift.py`):** synthetic scored history; assert threshold crossings and edge cases (empty windows, single-sample windows).
- **Integration (`tests/eval/test_end_to_end.py`):** real `Run` objects loaded from a fixture trace dir → run scorer with a mocked judge → verify `scored.jsonl` shape and dashboard loader join.
- **CLI (`tests/eval/test_cli.py`):** argparse paths, idempotency check, `--dry-run` writes nothing.

## Open Questions Deferred to v1.1

- Alert delivery (Slack/webhook/email).
- Per-tool rubrics (judge specifically grades tool call appropriateness).
- Human override / annotation (mark a scored run as "actually fine, judge was wrong"); feeds a future calibration pass.
- Score migration when `scorer_version` bumps (rescoring 100k runs efficiently).
- Aggregation by `session_id` (multi-turn quality vs. per-run quality).
- Per-user / per-team scoring once `OrgMemoryService` lands and `Run` carries scope.

## Related

- `nuvel/traces_cli.py` — input boundary (`Run`, `_discover_trace_dirs`).
- `nuvel/dashboard/loader.py` — augmented with scored-file side-load.
- `[[2026-05-15-org-memory-service-v1-design]]` — adjacent spec; eval will eventually score per-scope once that lands.
- `nuvel/plugins/skill_curator_plugin.py` — distant relative; both are "make the agent better over time" loops, but different mechanisms.
