# Eval Replay A/B v1 — Design

**Status:** Validated against codebase — pending owner review
**Date:** 2026-05-20
**Owner:** @Folken2

## Context

`nuvel eval` (PR #35) scores historical traces. That closes the *observation* loop but not the *intervention* loop — once a worst-runs report says "Haiku consistently lower than Sonnet on greetings," there is no way to ask "**what would the score have been with a different prompt?**" without running the agent again on new traffic and waiting for organic samples to accumulate.

Replay A/B v1 fills that gap. Given a **variant** (a config delta — new system prompt, different model, alternate temperature), replay it against the `user_input` of every historical trace, judge each replay with the existing rubric, and compare the aggregate scores against the original `scored.jsonl` baseline. Output goes to `replays/<variant_name>.jsonl` so multiple variants can coexist.

This is **option (3) from the original eval scoping** ("Replay + A/B"), now arrived at after seeing what option (1) unlocks. Per the recommendation: ship offline replay before automating prompt optimization, because the failure mode of automation — judge overfitting — is much easier to mitigate when humans propose variants first.

## Goals

1. Replay a single variant against historical trace `user_input`s and produce a scored output stream.
2. Compare aggregate scores between the variant and the original `scored.jsonl` baseline, per agent, with sample size made visible.
3. Variants are versioned, declarative YAML — same convention shape as `evals/rubric.yaml`.
4. Reuse the existing judge + rubric machinery; do not fork scoring.
5. Per-variant idempotency: re-running with the same `(trace_id, variant_version)` is a no-op.

## Non-Goals (v1)

- **Full multi-turn agent replay.** v1 replays only the single LLM call given the variant's `system_prompt` + the historical `user_input`. Tool use, memory recall, and follow-up turns are *not* re-executed. Honest limitation; called out in CLI output.
- **Automatic variant generation.** No optimizer / LLM-proposes-prompt loop. Humans write variants. v3 territory.
- **Online traffic splitting.** No live A/B against production users. Offline only.
- **Statistical significance testing.** v1 displays N alongside means + a warning when N<30; no t-test / bootstrap. Defer until usage justifies it.
- **Cross-agent variants.** A variant belongs to one agent. No global variants.
- **Judge holdout / validation splits.** v1 scores the full corpus with the same rubric. Holdout protocols come when (and if) automation lands.

## Approaches Considered

| # | Approach | Verdict |
|---|---|---|
| A | Full agent replay (spin up ADK runner with variant config, send `user_input`, capture new trace) | Rejected for v1 — replay state (memory, time-of-day deps, downstream tools) makes it fragile and expensive; tractable for v2 |
| B | LLM-only replay (one litellm call: variant system + historical user → variant output) | **Chosen** — cheap, decoupled, faithful for prompt-level questions which is the 80% case |
| C | Side-by-side live A/B in production | Rejected — needs governance pillar (#5), kill switches, per-user assignment; out of scope |
| D | Score-only synthesis (don't actually replay, ask the judge to *predict* the variant's score) | Rejected — synthetic; high judge bias; no real output to inspect |

## Architecture

```
nuvel/eval/
  replay/
    __init__.py
    schema.py        # Variant, ReplayResult, REPLAY_VERSION
    variant.py       # YAML loader + validation
    runner.py        # ReplayRunner — LLM call orchestrator
    compare.py       # baseline-vs-variant aggregation
  cli.py             # gains `replay`, `compare`, `variants` subcommands
```

Module boundaries:

- **`runner.py`** owns the litellm call shape, reusing `judge._call_litellm` style via composition. One job: given a Variant + a trace's `user_input`, produce an output string. Knows nothing about scoring or comparison.
- **`variant.py`** loads + validates YAML. No I/O beyond reading the file. Schema is small and stable.
- **`compare.py`** is pure functions over scored data — takes baseline `ScoredRun`s + variant `ReplayResult`s, returns a per-trace + aggregate diff. No I/O.
- **CLI** orchestrates: load variant → load traces → run + score → write replays.jsonl → render comparison.

## Variant Model

`generated-agents/<agent>/evals/variants/<variant-name>.yaml`:

```yaml
version: friendlier-tone-1.0
name: friendlier-tone
description: |
  Test whether a warmer system prompt improves quality on short greetings,
  which the eval surfaced as a low-score band.

# REQUIRED. The system prompt the variant runs under.
system_prompt: |
  Hey! I'm your agent. Let me know what you'd like to do — happy to start
  with examples if you're not sure yet.

# OPTIONAL. Defaults: model = EVAL_JUDGE_MODEL → DEFAULT_FAST_MODEL; temperature 0.0.
# Override to test a different model OR creative temperature.
model: openrouter/anthropic/claude-haiku-4.5
temperature: 0.2
max_tokens: 600
```

Notes:
- `version` is the idempotency key. Bumping it is a deliberate signal to rescore.
- v1 deliberately does **not** introspect the agent's current source to find a baseline prompt. The user's existing `scored.jsonl` (produced by `nuvel eval score` on real traces) **is** the baseline. The variant is the alternative hypothesis. Apples-to-apples is preserved as long as the rubric matches.
- A variant with only a `system_prompt` change is the canonical case; the other fields exist so the same machinery handles model swaps and temperature sweeps.

## Replay Semantics

```python
async def replay_run(run: Run, variant: Variant) -> ReplayResult: ...
```

Steps:
1. Extract `user_input` from `run` (already on the `Run` summary).
2. Construct messages: `[{role: "system", content: variant.system_prompt}, {role: "user", content: run.user_input}]`.
3. Call `litellm.acompletion(model=variant.model_resolved(), messages=..., temperature=variant.temperature, max_tokens=variant.max_tokens)`.
4. Reuse the empty-content fallback from `judge._call_litellm` (Kimi-via-OpenRouter compatibility).
5. Score the resulting output using `score_run` with a *synthetic* `Run` shaped just enough for the judge:
   - `user_input` = the historical user input
   - `events` = a single fake `llm_response` event carrying the variant's output as `response_text`
   - Schema = `"adk"` so heuristics behave; but heuristics are mostly NO-OP on a synthetic single-turn replay (no tools, no `tool_loop`, no `excessive_turns`)
6. Wrap in a `ReplayResult` that includes both the raw output AND the resulting `ScoredRun`.

**Why score via the existing scorer rather than calling the judge directly:** keeps the rubric + flag floor + components math in one place. Heuristics on a single-turn synthetic trace are mostly inert, which is fine — quality is the signal that matters here.

## ReplayResult Schema

`replays/<variant-name>.jsonl`, one object per `(trace_id, variant_version)`:

```json
{
  "trace_id": "abc123",
  "agent": "outlook-king",
  "variant_name": "friendlier-tone",
  "variant_version": "friendlier-tone-1.0",
  "replayed_at": "2026-05-21T12:00:00+00:00",
  "model": "openrouter/anthropic/claude-haiku-4.5",
  "output_text": "Hey! Happy to help — …",
  "replay_cost_usd": 0.00041,
  "scored": { /* ScoredRun shape — identical to scored.jsonl row */ }
}
```

Idempotency key: `(trace_id, variant_version)`. Bumping `version` in the YAML triggers a full rescore of that variant. Output is append-only; last-write-wins on read.

## Comparison Semantics

`nuvel eval compare <variant>` produces a per-agent diff:

| Agent | N | Baseline mean | Variant mean | Δ overall | Δ quality | Δ success | Wins | Ties | Losses |
|-------|---|---------------|--------------|-----------|-----------|-----------|------|------|--------|
| outlook-king | 47 | 0.74 | 0.81 | +0.07 | +0.10 | +0.04 | 31 | 9 | 7 |

Where:
- **N** = paired traces (each must exist in both `scored.jsonl` and the variant's replay file).
- **Δ** columns = mean of per-trace deltas (paired).
- **Wins / Ties / Losses** = count of per-trace deltas > 0 / == 0 / < 0.

If `N < 30`, append a warning row: *"sample too small for reliable conclusions — collect more traces or pair with `nuvel eval score` to fill gaps."*

Pure-function shape: `compare(baseline: list[ScoredRun], variant: list[ReplayResult]) -> ComparisonReport`. Easy to test, easy to extend with t-test later.

## CLI Surface

```
nuvel eval variants [--agent X]
nuvel eval replay <variant-name> [--agent X] [--since 7d] [--max-cost-usd 1.00] [--force] [--dry-run]
nuvel eval compare <variant-name> [--agent X] [--since 7d]
```

- **`variants`** — list discovered `evals/variants/*.yaml` across `generated-agents/`. Shows name, version, description, target agent.
- **`replay`** — execute replay against matching traces. Idempotent on `(trace_id, variant_version)` unless `--force`. Respects `--max-cost-usd` budget across both the replay call AND the judge call. Concurrency reused from scorer (semaphore=5).
- **`compare`** — render the diff table. Reads `scored.jsonl` for baseline and `replays/<variant>.jsonl` for variant. Pure read, no mutation. Exits with code 2 if a regression (Δ overall < -0.05) is detected — wires into a future alert layer the same way `drift` does.

## Cost Budgeting

Each replayed trace = **2 LLM calls** (one for the variant output, one for the judge to score it). At Kimi K2.5 (effectively $0 via OpenRouter today) the cost is negligible; at Haiku 4.5 ~$0.001 / trace. Default `--max-cost-usd 1.00` ≈ 1000 traces, same as `nuvel eval score`. Budget is enforced across BOTH call types.

When the budget exhausts:
- In-flight replays complete and get scored.
- New replay calls stop. No partial-replay writes — a trace either appears in the variant file with full data or doesn't appear at all.

## Idempotency & Versioning

- `(trace_id, variant_version)` is the dedup key. Re-running with no version bump is a no-op except for traces that don't yet appear in the variant file.
- Bumping `version` in the YAML → all rows are stale → next `replay` rescores everything (same model used by `SCORER_VERSION` in the eval harness).
- A separate constant `REPLAY_VERSION = "1.0"` versions the replay *machinery* (prompt assembly, message shape). Bumped only when the replay logic itself changes, not for variant tweaks.

## Storage Layout

```
generated-agents/<agent>/
  traces/
    2026-05-20.jsonl
    scored.jsonl          # from nuvel eval score
    replays/
      friendlier-tone.jsonl
      haiku-instead-of-kimi.jsonl
  evals/
    rubric.yaml           # existing
    variants/
      friendlier-tone.yaml
      haiku-instead-of-kimi.yaml
```

No change to `_RESERVED_TRACE_SIBLINGS` is required. `replays/` is a *subdirectory* of `traces/`, and trace discovery cannot reach it: `_discover_trace_dirs` only registers `./traces`, `generated-agents/*/traces`, and `$TRACE_DIR` (no recursion into a traces dir), and `_iter_trace_files` globs `*.jsonl` non-recursively. The earlier re-ingestion bug applied to *sibling files* like `scored.jsonl` sitting directly in a traces dir — a subdirectory does not reproduce it. The only edge case is an operator explicitly passing `traces/replays/` as `--source`; that is out of scope for v1 and, if it ever matters, is handled by skipping files whose parent dir is `replays/`, not by a filename reservation.

## Error Handling

| Condition | Behavior |
|---|---|
| `user_input` missing on the source trace | Skip the trace; record `skipped="no_user_input"` in summary; do not write a ReplayResult |
| Replay LLM call fails | Retry once (mirrors judge retry); on second failure, record `replay.error`, skip judging, no ReplayResult written |
| Judge call fails on the replayed output | Same as `nuvel eval score` — heuristics-only score, error captured in `scored.judge.error` |
| Variant YAML invalid | Fail fast before any LLM call |
| Baseline `scored.jsonl` missing for `compare` | Print clear hint: "run `nuvel eval score` first, then re-run compare"; exit 1 |
| Variant + baseline rubric versions disagree | Warn; show comparison anyway (operator opt-in); future work: refuse |

## Testing

- **Unit (`variant.py`):** YAML schema validation, version requirement, missing-fields handling.
- **Unit (`runner.py`):** mocked litellm — verify message construction (system + user), variant override priority, empty-content fallback inherited correctly.
- **Unit (`compare.py`):** paired vs unpaired counting, Δ math, N<30 warning, regression-exit threshold.
- **Integration (`tests/test_eval_replay_integration.py`):** synthesize a trace dir + a `scored.jsonl` + a variant YAML, run `ReplayRunner`, verify the variant file is written with correct shape and `compare()` produces a valid report.
- **CLI:** argparse paths for all three subcommands, idempotency check, `--dry-run` writes nothing.

Reuse the existing `_fake_response` test helper from `test_eval_judge.py`.

## Open Questions Deferred to v2

- **Full multi-turn replay** — re-execute the agent through ADK with variant config; replays tool dynamics and memory recall. Requires solving determinism around tool outputs.
- **Automatic variant generation** — LLM reads worst-runs notes, proposes prompt edits, drives replay-A/B in a loop. The risk surface (judge overfitting, reward hacking) needs holdout discipline before this can be safe.
- **Per-trace pairing for online A/B** — once the variant wins offline, route N% of real users to it with a kill switch. Needs governance (pillar #5).
- **Bootstrap confidence intervals / paired t-test** on the comparison table. Trivial to add — defer until N grows enough to matter.
- **Variant inheritance** — chain variants (`based_on: friendlier-tone`) so prompt evolution is auditable.
- **Multi-judge consensus** for replay scoring — same judge that wrote the original `scored.jsonl` also scores the replay, which biases toward its own preferences. Two-judge or rotating-judge protocols are a real v2 concern.

## Related

- [[2026-05-20-eval-harness-v1-design]] — the substrate this builds on. Replay reuses judge, rubric, scorer schema, and writer patterns wholesale.
- [[reference-nuvel-engine-pillars]] — replay A/B is pillar #2 extending into a feedback loop; not its own pillar.
- `nuvel/eval/judge.py` `_call_litellm` — the empty-content fallback discovered during the eval harness smoke test directly applies here.
- `nuvel/plugins/trace_plugin.py` — confirms `user_input` is in `run_start` events; system prompt is NOT, which informed the "variant declares both prompts" design choice.
