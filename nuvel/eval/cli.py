"""nuvel eval — CLI surface.

Subcommands:
    score   — apply scorer over discovered traces, write scored.jsonl
    report  — per-agent summary table
    worst   — N worst-scoring runs (triage entry point)
    drift   — rolling-window drift detection per agent

Wiring matches the existing `traces_cli.register` convention.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from nuvel.eval.drift import detect_drift
from nuvel.eval.replay.compare import compare
from nuvel.eval.replay.runner import ReplayRunner
from nuvel.eval.replay.schema import load_replay_index
from nuvel.eval.replay.variant import discover_variants, load_variant
from nuvel.eval.report import render_comparison, render_drift, render_report, render_variants, render_worst
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


def _cmd_variants(args: argparse.Namespace) -> int:
    print(render_variants(discover_variants(agent_filter=args.agent)))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    matches = [r for r in discover_variants(agent_filter=args.agent)
               if r.variant.name == args.variant_name]
    if not matches:
        print(f"no variant named '{args.variant_name}' found "
              f"(looked in generated-agents/*/evals/variants/).")
        return 1
    if len(matches) > 1:
        agents = ", ".join(sorted(m.agent for m in matches))
        print(f"variant '{args.variant_name}' exists for multiple agents ({agents}); "
              f"disambiguate with --agent.")
        return 1
    row = matches[0]
    # Re-load to surface any YAML error that discovery swallowed.
    variant = load_variant(row.path)
    since = _parse_since(getattr(args, "since", "") or "")
    runner = ReplayRunner(
        variant=variant,
        traces_dir=row.traces_dir,
        agent=row.agent,
        since=since,
        max_cost_usd=args.max_cost_usd,
        concurrency=args.concurrency,
        force=args.force,
        dry_run=args.dry_run,
    )
    report = asyncio.run(runner.run())
    print(f"Replayed: {report.replayed}  (variant '{variant.name}' @ {variant.version})")
    print(f"  skipped (already replayed at this version): {report.skipped_existing}")
    print(f"  skipped (no user_input on source trace):    {report.skipped_no_input}")
    print(f"  replay errors: {report.replay_errors}")
    print(f"  total cost (replay + judge): ${report.total_cost_usd:.4f}")
    if report.budget_exhausted:
        print(f"  ⚠ budget of ${args.max_cost_usd:.2f} exhausted — remaining traces not replayed")
    print("\nNote: v1 replays the single LLM call (variant system prompt + historical "
          "user_input). Tool use, memory recall, and follow-up turns are NOT re-executed.")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    matches = [r for r in discover_variants(agent_filter=args.agent)
               if r.variant.name == args.variant_name]
    if not matches:
        print(f"no variant named '{args.variant_name}' found.")
        return 1
    baseline = []
    variant_results = []
    for row in matches:
        baseline.extend(load_scored_index(row.traces_dir / "scored.jsonl").values())
        replay_path = row.traces_dir / "replays" / f"{row.variant.name}.jsonl"
        variant_results.extend(load_replay_index(replay_path).values())
    if not baseline:
        print("no baseline scored.jsonl found — run `nuvel eval score` first, then re-run compare.")
        return 1
    report = compare(baseline, variant_results)
    print(render_comparison(report))
    return 2 if report.regressed else 0


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

    p_variants = sub.add_parser("variants", help="List discovered replay variants.")
    p_variants.add_argument("--agent", "-a", default=None, help="Filter by agent (substring).")
    p_variants.set_defaults(func=_cmd_variants)

    p_replay = sub.add_parser("replay", help="Replay a variant against historical traces.")
    p_replay.add_argument("variant_name", help="Variant name (see `nuvel eval variants`).")
    p_replay.add_argument("--agent", "-a", default=None, help="Disambiguate by agent (substring).")
    p_replay.add_argument("--since", default=None, help="Only traces at/after this date (YYYY-MM-DD, ISO, or Nd).")
    p_replay.add_argument("--max-cost-usd", type=float, default=1.0,
                          help="Stop past this total spend across replay + judge (default $1.00).")
    p_replay.add_argument("--concurrency", type=int, default=5, help="Max simultaneous replays (default 5).")
    p_replay.add_argument("--force", action="store_true", help="Re-replay even if already at this version.")
    p_replay.add_argument("--dry-run", action="store_true", help="Replay + score but write nothing.")
    p_replay.set_defaults(func=_cmd_replay)

    p_compare = sub.add_parser("compare", help="Diff a variant's replays against the baseline.")
    p_compare.add_argument("variant_name", help="Variant name to compare.")
    p_compare.add_argument("--agent", "-a", default=None, help="Filter by agent (substring).")
    p_compare.set_defaults(func=_cmd_compare)
