"""Pure-function diff: baseline ScoredRuns vs variant ReplayResults.

Pairs by ``trace_id`` (a trace must exist in both to count). Deltas are means
of per-trace differences. No I/O — the CLI loads the inputs and renders the
output.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuvel.eval.replay.schema import ReplayResult
from nuvel.eval.schema import ScoredRun

_SMALL_SAMPLE_N = 30
_REGRESSION_THRESHOLD = -0.05


@dataclass
class AgentComparison:
    """One agent's paired baseline-vs-variant aggregate."""

    agent: str
    n: int
    baseline_overall_mean: float
    variant_overall_mean: float
    d_overall: float
    d_quality: float
    d_success: float
    wins: int
    ties: int
    losses: int

    @property
    def small_sample(self) -> bool:
        return self.n < _SMALL_SAMPLE_N


@dataclass
class ComparisonReport:
    """All per-agent comparisons + a top-level regression flag."""

    agents: list[AgentComparison] = field(default_factory=list)

    @property
    def regressed(self) -> bool:
        return any(a.d_overall < _REGRESSION_THRESHOLD for a in self.agents)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _component(scored: dict, key: str) -> float:
    return float((scored.get("components") or {}).get(key) or 0.0)


def compare(
    baseline: list[ScoredRun], variant: list[ReplayResult]
) -> ComparisonReport:
    """Diff variant replays against the baseline, grouped per agent."""
    base_by_id = {b.trace_id: b for b in baseline}
    # Group variant results by agent, keeping only traces present in baseline.
    by_agent: dict[str, list[tuple[ScoredRun, ReplayResult]]] = {}
    for v in variant:
        b = base_by_id.get(v.trace_id)
        if b is None:
            continue
        by_agent.setdefault(v.agent, []).append((b, v))

    report = ComparisonReport()
    for agent in sorted(by_agent):
        pairs = by_agent[agent]
        d_overall, d_quality, d_success = [], [], []
        wins = ties = losses = 0
        base_overall, var_overall = [], []
        for b, v in pairs:
            vs = v.scored
            bo, vo = b.overall, float(vs.get("overall") or 0.0)
            base_overall.append(bo)
            var_overall.append(vo)
            do = vo - bo
            d_overall.append(do)
            d_quality.append(_component(vs, "quality") - float(b.components.get("quality", 0.0)))
            d_success.append(_component(vs, "success") - float(b.components.get("success", 0.0)))
            if do > 0:
                wins += 1
            elif do < 0:
                losses += 1
            else:
                ties += 1
        report.agents.append(
            AgentComparison(
                agent=agent,
                n=len(pairs),
                baseline_overall_mean=_mean(base_overall),
                variant_overall_mean=_mean(var_overall),
                d_overall=_mean(d_overall),
                d_quality=_mean(d_quality),
                d_success=_mean(d_success),
                wins=wins,
                ties=ties,
                losses=losses,
            )
        )
    return report
