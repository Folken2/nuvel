"""Tests for the evalv2 self-consistency judge."""
from __future__ import annotations

from nuvel.evalv2 import run_consistency
from nuvel.evalv2.suite import EvalExample, EvalSuite


def _suite() -> EvalSuite:
    return EvalSuite(name="s", skill="summarize")


def _example() -> EvalExample:
    return EvalExample(id="ex1", input="in")


def test_identical_outputs_full_agreement():
    executor = lambda suite, ex: "the same answer every time"  # noqa: E731
    result, outputs = run_consistency(executor, _suite(), _example(), runs=3, threshold=0.9)
    assert result.score == 1.0
    assert result.passed is True
    assert len(outputs) == 3


def test_alternating_outputs_low_agreement_flagged():
    seq = iter(["alpha answer one", "totally unrelated zzzz", "alpha answer one"])
    executor = lambda suite, ex: next(seq)  # noqa: E731
    result, outputs = run_consistency(executor, _suite(), _example(), runs=3, threshold=0.9)
    assert result.passed is False
    assert result.score < 0.9
    assert "note" in result.details


def test_runs_param_controls_call_count():
    calls = {"n": 0}

    def executor(suite, ex):
        calls["n"] += 1
        return "x"

    run_consistency(executor, _suite(), _example(), runs=5, threshold=0.9)
    assert calls["n"] == 5


def test_threshold_comparison_boundary():
    # Two identical + one different -> only 1 of 3 pairs agree -> 1/3 ≈ 0.333
    seq = iter(["hello world foo", "hello world foo", "zzz completely other"])
    executor = lambda suite, ex: next(seq)  # noqa: E731

    # threshold just below the agreement -> passes
    result, _ = run_consistency(executor, _suite(), _example(), runs=3, threshold=0.3)
    assert result.passed is True
    assert abs(result.score - (1 / 3)) < 1e-9

    # threshold above the agreement -> fails
    seq2 = iter(["hello world foo", "hello world foo", "zzz completely other"])
    result2, _ = run_consistency(lambda s, ex: next(seq2), _suite(), _example(), runs=3, threshold=0.4)
    assert result2.passed is False
