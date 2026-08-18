"""nuvel eval — production trace scorer (v1).

Two eval tools live in nuvel, doing two different jobs:

    nuvel eval    (this package)  — score *production traces* written by the
                                    trace plugins: score, report, worst, drift.
    nuvel evalv2  (nuvel.evalv2)  — *skill evaluation*: run a skill's eval/
                                    suite against a fresh executor: init, list,
                                    run, compare.

The A/B replay layer that once lived here (``nuvel.eval.replay``) was removed
in favor of evalv2's full-executor runner, which replays with real tool use and
memory rather than a single low-fidelity LLM call.

Public surface:
    ScoredRun      — wire format written to scored.jsonl
    JudgeResult    — output of the LLM judge call
    Flag           — string constants for heuristic flags
    SCORER_VERSION — bump when scoring logic or output schema changes

Spec: docs/superpowers/specs/2026-05-20-eval-harness-v1-design.md
"""
from __future__ import annotations

from nuvel.eval.schema import (
    SCORER_VERSION,
    Flag,
    JudgeResult,
    ScoredRun,
)

__all__ = [
    "SCORER_VERSION",
    "Flag",
    "JudgeResult",
    "ScoredRun",
]
