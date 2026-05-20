"""Deterministic heuristic rules over a single ``Run``.

Pure functions: no I/O, no LLM. Each rule reads the ``Run`` summary
fields plus the raw events list and emits flags + component penalties.

Component scoring model:
  - Start: success=1.0, efficiency=1.0, reliability=1.0
    (``quality`` is judge-only and stays None at the heuristic stage.)
  - Each flag applies a penalty per the spec table.
  - Floor at 0.0, ceiling at 1.0.
  - If heuristics hit a hard floor (no_assistant_output / incomplete_trace),
    the judge is skipped — there is no semantic question to answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nuvel.eval.schema import Flag
from nuvel.eval.stats import BaselineStats, _agent_key
from nuvel.traces_cli import Run


# Spec thresholds. Centralized so a future per-rubric override has one place to patch.
_EXCESSIVE_TURNS_THRESHOLD = 20
_TOOL_LOOP_THRESHOLD = 5


@dataclass
class HeuristicResult:
    """Output of ``apply_heuristics``."""

    flags: list[str] = field(default_factory=list)
    components: dict[str, float] = field(
        default_factory=lambda: {
            "success": 1.0,
            "efficiency": 1.0,
            "reliability": 1.0,
        }
    )
    skip_judge: bool = False


def _has_tool_error(events: list[dict]) -> bool:
    for ev in events:
        name = ev.get("event")
        if name == "tool_exception":
            return True
        if name == "tool_end" and ev.get("status") == "error":
            return True
    return False


def _has_llm_error(events: list[dict]) -> bool:
    return any(ev.get("event") == "llm_error" for ev in events)


def _has_assistant_output(run: Run) -> bool:
    """A run produced output if any llm_response carried completion tokens."""
    if run.completion_tokens > 0:
        return True
    # Fallback: presence of any llm_response event with non-empty function_calls.
    for ev in run.events:
        if ev.get("event") == "llm_response":
            fc = ev.get("function_calls") or []
            if fc:
                return True
    return False


def _is_incomplete(run: Run) -> bool:
    """ADK traces should always end with run_end. Absence = trace was cut off."""
    if run.schema != "adk":
        # CASDK is a single flat record; treat ended_at as the signal.
        return run.ended_at is None
    return not any(ev.get("event") == "run_end" for ev in run.events)


def _tool_loop(events: list[dict]) -> bool:
    """Same tool name called ≥N times consecutively in tool_start events."""
    streak = 0
    last = None
    for ev in events:
        if ev.get("event") != "tool_start":
            continue
        name = ev.get("tool")
        if name == last:
            streak += 1
            if streak >= _TOOL_LOOP_THRESHOLD:
                return True
        else:
            last = name
            streak = 1
    return False


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def apply_heuristics(
    run: Run, baseline: dict[str, BaselineStats] | None = None
) -> HeuristicResult:
    """Run every heuristic and return aggregated flags + components.

    ``baseline`` is the per-agent percentile snapshot from
    ``compute_baseline_stats``; missing entries (unknown agent, empty
    history) disable outlier flags rather than firing them.
    """
    res = HeuristicResult()
    events = run.events

    # Incomplete trace check first — it's the cheapest exit.
    if _is_incomplete(run):
        res.flags.append(Flag.INCOMPLETE_TRACE)
        res.components["success"] = 0.0
        res.skip_judge = True
        return res

    if _has_tool_error(events):
        res.flags.append(Flag.TOOL_ERROR)
        res.components["reliability"] = 0.0

    if _has_llm_error(events):
        res.flags.append(Flag.LLM_ERROR)
        res.components["reliability"] = 0.0

    if not _has_assistant_output(run):
        res.flags.append(Flag.NO_ASSISTANT_OUTPUT)
        res.components["success"] = 0.0
        res.skip_judge = True
        # Continue scanning — useful to also surface tool_error etc.

    # Excessive turns.
    turns = run.llm_calls or 0
    if turns > _EXCESSIVE_TURNS_THRESHOLD:
        res.flags.append(Flag.EXCESSIVE_TURNS)
        res.components["efficiency"] = _clamp(res.components["efficiency"] - 0.3)

    # Tool loop.
    if _tool_loop(events):
        res.flags.append(Flag.TOOL_LOOP)
        res.components["reliability"] = _clamp(res.components["reliability"] - 0.5)
        res.components["efficiency"] = _clamp(res.components["efficiency"] - 0.2)

    # Outliers — only fire if we have baseline data for this agent.
    if baseline is not None:
        stats = baseline.get(_agent_key(run))
        if stats is not None:
            if (
                stats.p95_cost_usd is not None
                and run.cost_usd is not None
                and run.cost_usd > stats.p95_cost_usd
            ):
                res.flags.append(Flag.COST_OUTLIER)
                res.components["efficiency"] = _clamp(
                    res.components["efficiency"] - 0.2
                )
            if (
                stats.p95_duration_ms is not None
                and run.duration_ms is not None
                and run.duration_ms > stats.p95_duration_ms
            ):
                res.flags.append(Flag.LATENCY_OUTLIER)
                res.components["efficiency"] = _clamp(
                    res.components["efficiency"] - 0.1
                )
            if (
                stats.p99_total_tokens is not None
                and run.total_tokens
                and run.total_tokens > stats.p99_total_tokens
            ):
                res.flags.append(Flag.TOKEN_BLOAT)
                res.components["efficiency"] = _clamp(
                    res.components["efficiency"] - 0.2
                )

    return res
