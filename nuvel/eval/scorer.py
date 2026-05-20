"""Scorer orchestrator: heuristics → judge → ScoredRun.

`score_run` is the per-run function. `ScoreSession` is the batch driver
that owns idempotency, concurrency, and the cost budget across many
runs. It writes one ``scored.jsonl`` per trace directory.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from nuvel.eval.heuristics import HeuristicResult, apply_heuristics
from nuvel.eval.judge import judge_run as _default_judge_run
from nuvel.eval.rubric import DEFAULT_RUBRIC, Rubric, load_rubric
from nuvel.eval.schema import SCORER_VERSION, JudgeResult, ScoredRun
from nuvel.eval.stats import BaselineStats, compute_baseline_stats
from nuvel.eval.writer import append_scored, load_scored_index
from nuvel.traces_cli import (
    Run,
    _agent_label_for,
    _collect_runs,
    _discover_trace_dirs,
)


logger = logging.getLogger(__name__)


# Type alias for the judge callable (injection seam for tests).
JudgeFn = Callable[[Run, Rubric], Awaitable[JudgeResult]]


_DEFAULT_CONCURRENCY = 5
_DEFAULT_BUDGET_USD = 1.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _weighted_overall(components: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean. Ignores components missing from ``weights`` and renormalizes."""
    total_weight = 0.0
    score = 0.0
    for key, w in weights.items():
        if key not in components:
            continue
        score += components[key] * w
        total_weight += w
    if total_weight == 0.0:
        return 0.0
    return score / total_weight


def _scored_from(
    run: Run,
    rubric: Rubric,
    heuristic: HeuristicResult,
    judge: JudgeResult | None,
) -> ScoredRun:
    """Compose a ``ScoredRun`` from heuristic + judge outputs."""
    components = dict(heuristic.components)
    # Quality is judge-only territory; default 0.0 if judge was skipped or failed.
    components.setdefault("quality", 0.0)

    judge_blob: dict = {}
    if judge is not None:
        judge_blob = {
            "model": judge.model,
            "cost_usd": judge.cost_usd,
            "notes": judge.notes,
        }
        if judge.error:
            judge_blob["error"] = judge.error
        if judge.ok:
            # Judge overrides heuristic success default, sets quality.
            components["success"] = judge.success if judge.success is not None else components["success"]
            components["quality"] = judge.quality if judge.quality is not None else 0.0

    overall = _weighted_overall(components, rubric.weights)

    return ScoredRun(
        trace_id=run.trace_id or run.session_id or "",
        agent=run.agent,
        scored_at=_now_iso(),
        scorer_version=SCORER_VERSION,
        rubric_version=rubric.version,
        overall=overall,
        components=components,
        flags=list(heuristic.flags),
        judge=judge_blob,
        skipped_judge=heuristic.skip_judge or judge is None,
    )


async def score_run(
    run: Run,
    *,
    rubric: Rubric = DEFAULT_RUBRIC,
    baseline: dict[str, BaselineStats] | None = None,
    judge_fn: JudgeFn | None = None,
    judge_disabled: bool = False,
) -> ScoredRun:
    """Score a single ``Run``. Pure compose of heuristics + (optional) judge.

    ``judge_disabled=True`` skips the judge unconditionally (used when the
    batch driver has blown its cost budget).
    """
    heuristic = apply_heuristics(run, baseline=baseline)
    judge: JudgeResult | None = None
    if not heuristic.skip_judge and not judge_disabled:
        fn = judge_fn or _default_judge_run
        judge = await fn(run, rubric)
    return _scored_from(run, rubric, heuristic, judge)


@dataclass
class ScoreReport:
    """Summary of a ``ScoreSession.run()`` invocation."""

    scored_count: int = 0
    skipped_existing: int = 0
    skipped_judge: int = 0
    judge_errors: int = 0
    total_cost_usd: float = 0.0
    budget_exhausted: bool = False
    per_dir: dict[Path, int] = field(default_factory=dict)


@dataclass
class ScoreSession:
    """Batch driver. One session = one ``nuvel eval score`` invocation."""

    sources: list[str] | None = None
    max_cost_usd: float = _DEFAULT_BUDGET_USD
    concurrency: int = _DEFAULT_CONCURRENCY
    force: bool = False
    dry_run: bool = False
    judge_fn: JudgeFn | None = None  # injection seam for tests
    rubric_resolver: Callable[[str], Rubric] = load_rubric

    async def run(self) -> ScoreReport:
        report = ScoreReport()
        dirs = _discover_trace_dirs(self.sources)
        if not dirs:
            logger.info("no trace directories found")
            return report

        all_runs = _collect_runs(self.sources, keep_events=True)
        baseline = compute_baseline_stats(all_runs)

        # Group runs by their containing trace directory.
        by_dir: dict[Path, list[Run]] = {}
        for r in all_runs:
            if not r.file:
                continue
            by_dir.setdefault(r.file.parent, []).append(r)

        budget_lock = asyncio.Lock()
        budget_state = {"spent": 0.0, "exhausted": False}

        async def _maybe_judge(run: Run, rubric: Rubric) -> JudgeResult:
            """Wrap the judge with a cost-budget check + accounting."""
            fn = self.judge_fn or _default_judge_run
            res = await fn(run, rubric)
            async with budget_lock:
                budget_state["spent"] += res.cost_usd
                if budget_state["spent"] >= self.max_cost_usd:
                    budget_state["exhausted"] = True
            return res

        sem = asyncio.Semaphore(self.concurrency)

        for dir_path, runs in by_dir.items():
            scored_path = dir_path / "scored.jsonl"
            existing = load_scored_index(scored_path)

            async def _score_one(r: Run) -> ScoredRun | None:
                tid = r.trace_id or r.session_id or ""
                if not tid:
                    return None
                if not self.force:
                    prior = existing.get(tid)
                    if prior is not None and prior.scorer_version == SCORER_VERSION:
                        return None  # skipped
                # Resolve rubric per top-level agent label, not the per-trace agent string
                rubric = self.rubric_resolver(_agent_label_for(r.file))
                async with sem:
                    scored = await score_run(
                        r,
                        rubric=rubric,
                        baseline=baseline,
                        judge_fn=_maybe_judge,
                        judge_disabled=budget_state["exhausted"],
                    )
                return scored

            tasks = [asyncio.create_task(_score_one(r)) for r in runs]
            results = await asyncio.gather(*tasks)

            for scored in results:
                if scored is None:
                    report.skipped_existing += 1
                    continue
                if scored.skipped_judge:
                    report.skipped_judge += 1
                judge_blob = scored.judge or {}
                if judge_blob.get("error"):
                    report.judge_errors += 1
                report.total_cost_usd += float(judge_blob.get("cost_usd") or 0.0)

                if not self.dry_run:
                    append_scored(scored_path, scored)
                report.scored_count += 1
                report.per_dir[dir_path] = report.per_dir.get(dir_path, 0) + 1

        report.budget_exhausted = budget_state["exhausted"]
        return report
