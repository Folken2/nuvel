"""nuvel eval — online trace scorer.

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
