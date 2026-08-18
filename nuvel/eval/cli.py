"""nuvel eval — production trace scoring.

This is the v1 tool, and it is a *different job* from ``nuvel evalv2``:

    nuvel eval    — score production traces written by the trace plugins
                    (score, report, worst, drift).
    nuvel evalv2  — skill evaluation: run a skill's eval/ suite against a
                    fresh executor (init, list, run, compare).

Subcommands:
    score   — apply scorer over discovered traces, write scored.jsonl
    report  — per-agent summary table
    worst   — N worst-scoring runs (triage entry point)
    drift   — rolling-window drift detection per agent

Wiring matches the existing `traces_cli.register` convention. The A/B replay
layer (``replay``/``compare``/``variants``) was removed in favor of evalv2's
full-executor runner.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from nuvel.eval.drift import detect_drift
from nuvel.eval.report import render_drift, render_report, render_worst
from nuvel.eval.scorer import ScoreSession
from nuvel.eval.writer import load_scored_index
from nuvel.traces_cli import _discover_trace_dirs, _parse_since


def _load_all_scored(sources: list[str] | None) -> list:
    """Aggregate every ``scored.jsonl`` across discovered trace directories."""
    out = []
    for d in _discover_trace_dirs(sources):
        idx = load_scored_index(d / "scored.jsonl")
        out.extend(idx.values())
    return out


def _apply_filters(scored: list, args: argparse.Namespace) -> list:
    agent = getattr(args, "agent", None)
    since = _parse_since(getattr(args, "since", "") or "")
    if agent:
        scored = [s for s in scored if agent.lower() in s.agent.lower()]
    if since:
        from datetime import timezone

        def _ts(s):
            try:
                t = datetime.fromisoformat(s.scored_at.replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                return t
            except ValueError:
                return None
        # Allow `since` without tz to match the file convention.
        target = since
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        scored = [s for s in scored if (_ts(s) is None or _ts(s) >= target)]
    return scored


def _cmd_score(args: argparse.Namespace) -> int:
    session = ScoreSession(
        sources=args.source,
        max_cost_usd=args.max_cost_usd,
        concurrency=args.concurrency,
        force=args.force,
        dry_run=args.dry_run,
    )
    report = asyncio.run(session.run())
    print(f"Scored: {report.scored_count}")
    print(f"  skipped (already scored at current version): {report.skipped_existing}")
    print(f"  judge skipped (heuristic floor / disabled): {report.skipped_judge}")
    print(f"  judge errors: {report.judge_errors}")
    print(f"  total judge cost: ${report.total_cost_usd:.4f}")
    if report.budget_exhausted:
        print(f"  ⚠ budget of ${args.max_cost_usd:.2f} exhausted — later runs scored heuristics-only")
    if report.scored_count == 0:
        return 0
    print("\nPer directory:")
    for d, n in sorted(report.per_dir.items()):
        print(f"  {n:>4}  {d}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    scored = _apply_filters(_load_all_scored(args.source), args)
    print(render_report(scored))
    return 0


def _cmd_worst(args: argparse.Namespace) -> int:
    scored = _apply_filters(_load_all_scored(args.source), args)
    print(render_worst(scored, n=args.n))
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    scored = _load_all_scored(args.source)
    if args.agent:
        scored = [s for s in scored if args.agent.lower() in s.agent.lower()]
    reports = detect_drift(scored, window_days=args.window_days, threshold=args.threshold)
    print(render_drift(reports))
    any_drift = any(r.drifted for r in reports)
    return 2 if any_drift else 0


def _add_source_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--source", "-s", action="append", default=None,
        help="Extra trace directory to scan (repeatable).",
    )


def _add_common_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--agent", "-a", default=None, help="Filter by agent name (substring).")
    p.add_argument("--since", default=None,
                   help="Only scored rows at/after this date (YYYY-MM-DD or ISO).")


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the `eval` subcommand tree."""
    p = subparsers.add_parser(
        "eval",
        help="Score agent traces (heuristics + LLM judge), report, drift.",
    )
    sub = p.add_subparsers(dest="eval_command", required=True)

    p_score = sub.add_parser("score", help="Score discovered traces; write scored.jsonl.")
    _add_source_flag(p_score)
    p_score.add_argument("--max-cost-usd", type=float, default=1.0,
                         help="Stop launching new judges past this total spend (default $1.00).")
    p_score.add_argument("--concurrency", type=int, default=5,
                         help="Max simultaneous judge calls (default 5).")
    p_score.add_argument("--force", action="store_true",
                         help="Rescore everything, ignoring existing scored.jsonl entries.")
    p_score.add_argument("--dry-run", action="store_true",
                         help="Run heuristics + judge but do not write scored.jsonl.")
    p_score.set_defaults(func=_cmd_score)

    p_report = sub.add_parser("report", help="Per-agent summary of scored runs.")
    _add_source_flag(p_report)
    _add_common_filters(p_report)
    p_report.set_defaults(func=_cmd_report)

    p_worst = sub.add_parser("worst", help="N worst-scoring runs.")
    _add_source_flag(p_worst)
    _add_common_filters(p_worst)
    p_worst.add_argument("-n", type=int, default=10, help="Max rows (default 10).")
    p_worst.set_defaults(func=_cmd_worst)

    p_drift = sub.add_parser("drift", help="Rolling-window drift detection per agent.")
    _add_source_flag(p_drift)
    p_drift.add_argument("--agent", "-a", default=None, help="Filter by agent name (substring).")
    p_drift.add_argument("--window-days", type=int, default=7,
                         help="Window length (default 7).")
    p_drift.add_argument("--threshold", type=float, default=0.1,
                         help="Flag drift when |delta| >= this (default 0.1).")
    p_drift.set_defaults(func=_cmd_drift)
