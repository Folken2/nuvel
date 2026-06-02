"""CLI wiring for variants/replay/compare. Uses the real argparse tree."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.eval.replay.compare import AgentComparison, ComparisonReport
from nuvel.eval.report import render_comparison, render_variants
from nuvel.eval.replay.variant import DiscoveredVariant
from nuvel.eval.replay.schema import Variant


def test_render_variants_lists_name_and_agent() -> None:
    rows = [DiscoveredVariant(
        agent="outlook-king",
        variant=Variant(version="v-1.0", name="friendlier", system_prompt="hi",
                        description="warm"),
        path=Path("x.yaml"),
        traces_dir=Path("t"),
    )]
    out = render_variants(rows)
    assert "friendlier" in out and "outlook-king" in out and "v-1.0" in out


def test_render_comparison_has_columns_and_warning() -> None:
    report = ComparisonReport(agents=[AgentComparison(
        agent="outlook-king", n=5, baseline_overall_mean=0.74, variant_overall_mean=0.81,
        d_overall=0.07, d_quality=0.1, d_success=0.04, wins=3, ties=1, losses=1)])
    out = render_comparison(report)
    assert "outlook-king" in out
    assert "+0.07" in out or "0.07" in out
    assert "sample" in out.lower()  # N<30 warning present


def _build_cli():
    import argparse
    from nuvel.eval.cli import register
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    register(sub)
    return parser


def test_cli_variants_parses() -> None:
    args = _build_cli().parse_args(["eval", "variants", "--agent", "outlook"])
    assert args.eval_command == "variants"
    assert args.agent == "outlook"


def test_cli_replay_parses_all_flags() -> None:
    args = _build_cli().parse_args(
        ["eval", "replay", "friendlier", "--agent", "outlook", "--since", "7d",
         "--max-cost-usd", "0.50", "--force", "--dry-run"])
    assert args.eval_command == "replay"
    assert args.variant_name == "friendlier"
    assert args.max_cost_usd == 0.50
    assert args.force is True and args.dry_run is True


def test_cli_compare_parses() -> None:
    args = _build_cli().parse_args(["eval", "compare", "friendlier", "--agent", "outlook"])
    assert args.eval_command == "compare"
    assert args.variant_name == "friendlier"


def test_cli_replay_unknown_variant_exits_1(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)  # no generated-agents → no variants
    args = _build_cli().parse_args(["eval", "replay", "ghost"])
    rc = args.func(args)
    assert rc == 1
    assert "no variant" in capsys.readouterr().out.lower()
