"""Self-consistency evaluator.

Runs the executor ``runs`` times on the same example and measures how much
the outputs agree. Two outputs "agree" when their difflib similarity ratio
is at least ``_AGREE_RATIO``; agreement is the fraction of output pairs that
agree. Below ``threshold`` the example is flagged — a model that answers the
same prompt inconsistently is a signal the runner surfaces to the caller.
"""
from __future__ import annotations

import difflib
from itertools import combinations
from typing import Callable

from ..schema import EvaluatorResult
from ..suite import EvalExample, EvalSuite


# Two outputs count as agreeing at or above this pairwise similarity.
_AGREE_RATIO = 0.8

Executor = Callable[[EvalSuite, EvalExample], str]


def _pairwise_agreement(outputs: list[str]) -> float:
    """Fraction of output pairs whose similarity ratio >= ``_AGREE_RATIO``."""
    if len(outputs) < 2:
        return 1.0
    pairs = list(combinations(outputs, 2))
    agree = sum(
        1 for a, b in pairs if difflib.SequenceMatcher(None, a, b).ratio() >= _AGREE_RATIO
    )
    return agree / len(pairs)


def run_consistency(
    executor: Executor,
    suite: EvalSuite,
    example: EvalExample,
    runs: int = 3,
    threshold: float = 0.9,
    max_cost: float | None = None,
) -> tuple[EvaluatorResult, list[str]]:
    """Run ``executor`` ``runs`` times and score output agreement.

    Returns ``(result, outputs)`` — the caller keeps ``outputs`` so it can
    cache or pick a representative output. When agreement drops below
    ``threshold`` the result is marked ``passed=False`` with a note.
    """
    runs = max(1, int(runs))
    outputs = [executor(suite, example) for _ in range(runs)]
    agreement = _pairwise_agreement(outputs)
    passed = agreement >= float(threshold)
    details: dict = {"runs": runs, "threshold": threshold, "agreement": round(agreement, 4)}
    if not passed:
        details["note"] = (
            f"self-consistency {agreement:.2f} below threshold {threshold:.2f} "
            f"over {runs} runs"
        )
    if max_cost is not None:
        details["max_cost"] = max_cost
    result = EvaluatorResult(
        evaluator="self-consistency",
        name="self-consistency",
        score=agreement,
        passed=passed,
        details=details,
    )
    return result, outputs
