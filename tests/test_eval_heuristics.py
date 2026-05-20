"""Heuristic flag rules."""
from __future__ import annotations

from pathlib import Path

from nuvel.eval.heuristics import apply_heuristics
from nuvel.eval.schema import Flag
from nuvel.eval.stats import BaselineStats
from nuvel.traces_cli import Run


def _adk_run(
    *,
    agent: str = "test-agent",
    events: list[dict] | None = None,
    duration_ms: int = 100,
    llm_calls: int = 1,
    completion_tokens: int = 50,
    total_tokens: int = 100,
    cost_usd: float | None = 0.001,
) -> Run:
    """Build an ADK Run with run_end present by default."""
    if events is None:
        events = [
            {"event": "run_start", "user_input": "hi"},
            {"event": "llm_response", "usage": {"completion_tokens": completion_tokens}},
            {"event": "run_end", "duration_ms": duration_ms},
        ]
    return Run(
        agent=agent,
        file=Path("/tmp/x.jsonl"),
        session_id="s",
        trace_id="t",
        schema="adk",
        events=events,
        duration_ms=duration_ms,
        llm_calls=llm_calls,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        ended_at="2026-05-20T00:00:00+00:00",
    )


def test_clean_run_has_no_flags() -> None:
    res = apply_heuristics(_adk_run())
    assert res.flags == []
    assert res.components["success"] == 1.0
    assert res.components["efficiency"] == 1.0
    assert res.components["reliability"] == 1.0
    assert res.skip_judge is False


def test_tool_error_drops_reliability() -> None:
    run = _adk_run(events=[
        {"event": "run_start", "user_input": "hi"},
        {"event": "tool_start", "tool": "x"},
        {"event": "tool_end", "tool": "x", "status": "error"},
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ])
    res = apply_heuristics(run)
    assert Flag.TOOL_ERROR in res.flags
    assert res.components["reliability"] == 0.0
    assert res.skip_judge is False


def test_tool_exception_also_flags_tool_error() -> None:
    run = _adk_run(events=[
        {"event": "run_start"},
        {"event": "tool_exception", "tool": "x"},
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ])
    res = apply_heuristics(run)
    assert Flag.TOOL_ERROR in res.flags


def test_llm_error_drops_reliability() -> None:
    run = _adk_run(events=[
        {"event": "run_start"},
        {"event": "llm_error", "model": "x"},
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ])
    res = apply_heuristics(run)
    assert Flag.LLM_ERROR in res.flags
    assert res.components["reliability"] == 0.0


def test_no_assistant_output_skips_judge() -> None:
    run = _adk_run(
        completion_tokens=0,
        events=[
            {"event": "run_start"},
            {"event": "run_end"},
        ],
    )
    res = apply_heuristics(run)
    assert Flag.NO_ASSISTANT_OUTPUT in res.flags
    assert res.components["success"] == 0.0
    assert res.skip_judge is True


def test_incomplete_trace_short_circuits() -> None:
    # ADK run with no run_end event → incomplete.
    run = _adk_run(events=[{"event": "run_start"}])
    res = apply_heuristics(run)
    assert Flag.INCOMPLETE_TRACE in res.flags
    assert res.skip_judge is True
    assert res.components["success"] == 0.0


def test_excessive_turns() -> None:
    run = _adk_run(llm_calls=25)
    res = apply_heuristics(run)
    assert Flag.EXCESSIVE_TURNS in res.flags
    assert abs(res.components["efficiency"] - 0.7) < 1e-9


def test_tool_loop() -> None:
    events = [{"event": "run_start"}]
    events += [{"event": "tool_start", "tool": "x"} for _ in range(5)]
    events += [
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ]
    run = _adk_run(events=events)
    res = apply_heuristics(run)
    assert Flag.TOOL_LOOP in res.flags
    assert res.components["reliability"] == 0.5
    assert abs(res.components["efficiency"] - 0.8) < 1e-9


def test_tool_loop_with_breaks_does_not_fire() -> None:
    events = [
        {"event": "run_start"},
        {"event": "tool_start", "tool": "x"},
        {"event": "tool_start", "tool": "x"},
        {"event": "tool_start", "tool": "y"},  # break
        {"event": "tool_start", "tool": "x"},
        {"event": "tool_start", "tool": "x"},
        {"event": "tool_start", "tool": "x"},
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ]
    res = apply_heuristics(_adk_run(events=events))
    assert Flag.TOOL_LOOP not in res.flags


def test_cost_outlier() -> None:
    run = _adk_run(cost_usd=0.05)
    baseline = {"test-agent": BaselineStats(p95_cost_usd=0.01)}
    res = apply_heuristics(run, baseline=baseline)
    assert Flag.COST_OUTLIER in res.flags
    assert abs(res.components["efficiency"] - 0.8) < 1e-9


def test_latency_outlier() -> None:
    run = _adk_run(duration_ms=5000)
    baseline = {"test-agent": BaselineStats(p95_duration_ms=1000.0)}
    res = apply_heuristics(run, baseline=baseline)
    assert Flag.LATENCY_OUTLIER in res.flags


def test_token_bloat() -> None:
    run = _adk_run(total_tokens=10000)
    baseline = {"test-agent": BaselineStats(p99_total_tokens=5000.0)}
    res = apply_heuristics(run, baseline=baseline)
    assert Flag.TOKEN_BLOAT in res.flags


def test_baseline_absent_disables_outlier_flags() -> None:
    run = _adk_run(cost_usd=999.99, duration_ms=999999, total_tokens=999999)
    res = apply_heuristics(run, baseline=None)
    assert Flag.COST_OUTLIER not in res.flags
    assert Flag.LATENCY_OUTLIER not in res.flags
    assert Flag.TOKEN_BLOAT not in res.flags


def test_multiple_flags_compose_penalties() -> None:
    # excessive_turns (-0.3 efficiency) + cost_outlier (-0.2) + token_bloat (-0.2)
    run = _adk_run(llm_calls=25, cost_usd=0.5, total_tokens=20000)
    baseline = {
        "test-agent": BaselineStats(
            p95_cost_usd=0.01,
            p95_duration_ms=10000.0,
            p99_total_tokens=5000.0,
        ),
    }
    res = apply_heuristics(run, baseline=baseline)
    # 1.0 - 0.3 - 0.2 - 0.2 = 0.3
    assert abs(res.components["efficiency"] - 0.3) < 1e-9
    assert {Flag.EXCESSIVE_TURNS, Flag.COST_OUTLIER, Flag.TOKEN_BLOAT}.issubset(set(res.flags))


def test_floor_at_zero() -> None:
    # Stack penalties past zero; should clamp.
    events = [{"event": "run_start"}]
    events += [{"event": "tool_start", "tool": "x"} for _ in range(5)]
    events += [
        {"event": "llm_response", "usage": {"completion_tokens": 5}},
        {"event": "run_end"},
    ]
    run = _adk_run(events=events, llm_calls=25, cost_usd=0.5, total_tokens=20000)
    baseline = {
        "test-agent": BaselineStats(
            p95_cost_usd=0.01,
            p95_duration_ms=10000.0,
            p99_total_tokens=5000.0,
        ),
    }
    res = apply_heuristics(run, baseline=baseline)
    # Tool_loop (-0.2) + excessive (-0.3) + cost (-0.2) + tokens (-0.2) = -0.9 → 0.1
    assert res.components["efficiency"] >= 0.0
    assert res.components["reliability"] >= 0.0
