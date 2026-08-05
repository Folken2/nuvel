"""Eval replay A/B: replay config variants against historical traces.

Layered on top of the eval harness — reuses judge, rubric, scorer, and the
ScoredRun wire format wholesale. See
docs/superpowers/specs/2026-05-20-eval-replay-ab-v1-design.md.
"""
from nuvel.eval.replay.runner import ReplayReport, ReplayRunner, replay_run
from nuvel.eval.replay.schema import (
    REPLAY_VERSION,
    ReplayResult,
    Variant,
    append_replay,
    load_replay_index,
)

__all__ = [
    "REPLAY_VERSION",
    "ReplayReport",
    "ReplayRunner",
    "ReplayResult",
    "Variant",
    "append_replay",
    "load_replay_index",
    "replay_run",
]
