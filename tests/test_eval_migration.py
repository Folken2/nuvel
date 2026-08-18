"""Migration guardrails — the v1 replay A/B layer was removed in favor of evalv2.

Trace scoring (score/report/worst/drift) stays; replay/compare/variants and the
``nuvel.eval.replay`` module are gone.
"""
from __future__ import annotations

import importlib

import pytest

from nuvel.cli import build_parser


def _eval_subcommands() -> set[str]:
    parser = build_parser()
    # Drill into the `eval` subparser and read its registered command names.
    sub_action = next(
        a for a in parser._actions if getattr(a, "dest", None) == "command"
    )
    eval_parser = sub_action.choices["eval"]
    eval_sub = next(
        a for a in eval_parser._actions if getattr(a, "dest", None) == "eval_command"
    )
    return set(eval_sub.choices)


def test_replay_subcommands_removed() -> None:
    commands = _eval_subcommands()
    assert "replay" not in commands
    assert "compare" not in commands
    assert "variants" not in commands


def test_trace_scoring_subcommands_kept() -> None:
    commands = _eval_subcommands()
    assert {"score", "report", "worst", "drift"} <= commands


def test_replay_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nuvel.eval.replay")
