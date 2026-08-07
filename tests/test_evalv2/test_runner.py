"""Tests for the evalv2 runner — fake executors/judges, no network."""
from __future__ import annotations

import json

from nuvel.evalv2 import EvalRunConfig, EvalRunner, SampleCache
from nuvel.evalv2.suite import EvalExample, EvalSuite


def _suite(evaluators=None, examples=None, thresholds=None) -> EvalSuite:
    return EvalSuite(
        name="summarize-eval",
        skill="summarize",
        evaluators=evaluators or [],
        thresholds=thresholds or {"pass": 0.8, "warn": 0.6},
        examples=examples or [],
    )


def _ex(id_: str, expected: str | None = None) -> EvalExample:
    return EvalExample(id=id_, input=f"input-{id_}", expected_output=expected)


def test_fake_executor_and_judge_summary():
    suite = _suite(
        evaluators=[{"llm-judge": {"rubric": {"accuracy": 1.0}}}],
        examples=[_ex("a"), _ex("b")],
    )
    scores = iter([0.95, 0.4])
    config = EvalRunConfig(
        executor=lambda s, ex: "output",
        judge_fn=lambda prompt: json.dumps({"accuracy": next(scores)}),
    )
    result = EvalRunner(suite, config).run()
    assert result.summary.total == 2
    assert result.summary.passed == 1
    assert result.summary.failed == 1
    assert result.summary.unscored == 0
    assert result.skill == "summarize"


def test_cache_hit_path(tmp_path):
    cache = SampleCache(cache_dir=tmp_path / "cache")
    suite = _suite(
        evaluators=[{"llm-judge": {"rubric": {"accuracy": 1.0}}}],
        examples=[_ex("a")],
    )
    calls = {"n": 0}

    def executor(s, ex):
        calls["n"] += 1
        return "output"

    config = EvalRunConfig(
        executor=executor,
        judge_fn=lambda prompt: json.dumps({"accuracy": 0.9}),
        cache=cache,
    )
    first = EvalRunner(suite, config).run()
    second = EvalRunner(suite, config).run()

    assert calls["n"] == 1  # executor only ran on the first pass
    assert first.examples[0].cache_hit is False
    assert second.examples[0].cache_hit is True
    assert second.examples[0].score == 0.9


def test_cache_miss_on_different_input(tmp_path):
    cache = SampleCache(cache_dir=tmp_path / "cache")
    calls = {"n": 0}

    def executor(s, ex):
        calls["n"] += 1
        return "output"

    config = EvalRunConfig(
        executor=executor,
        judge_fn=lambda prompt: json.dumps({"accuracy": 0.9}),
        cache=cache,
    )
    EvalRunner(_suite(
        evaluators=[{"llm-judge": {"rubric": {"accuracy": 1.0}}}],
        examples=[_ex("a")],
    ), config).run()
    EvalRunner(_suite(
        evaluators=[{"llm-judge": {"rubric": {"accuracy": 1.0}}}],
        examples=[_ex("b")],
    ), config).run()

    assert calls["n"] == 2  # different inputs -> two executor runs


def test_no_evaluators_unscored():
    suite = _suite(evaluators=[], examples=[_ex("a")])
    config = EvalRunConfig(executor=lambda s, ex: "output")
    result = EvalRunner(suite, config).run()
    assert result.examples[0].score is None
    assert result.summary.unscored == 1
    assert result.summary.overall is None


def test_consistency_disagreement_flag():
    suite = _suite(
        evaluators=[{"self-consistency": {"runs": 3, "threshold": 0.9}}],
        examples=[_ex("a")],
    )
    seq = iter(["alpha answer", "totally different zzz", "alpha answer"])
    config = EvalRunConfig(executor=lambda s, ex: next(seq))
    result = EvalRunner(suite, config).run()
    assert any(f["type"] == "judge-disagreement" for f in result.flags)
    assert result.flags[0]["example"] == "a"


def test_progress_callback_per_example():
    suite = _suite(
        evaluators=[{"llm-judge": {"rubric": {"accuracy": 1.0}}}],
        examples=[_ex("a"), _ex("b")],
    )
    messages: list[str] = []
    config = EvalRunConfig(
        executor=lambda s, ex: "output",
        judge_fn=lambda prompt: json.dumps({"accuracy": 0.9}),
    )
    EvalRunner(suite, config).run(progress=messages.append)
    assert len(messages) == 2
    assert any("a" in m for m in messages)


def test_composite_from_multiple_evaluators():
    # llm-judge 0.9 + deterministic exact-match 1.0 -> composite 0.95
    suite = _suite(
        evaluators=[
            {"llm-judge": {"rubric": {"accuracy": 1.0}}},
            {"deterministic": [{"type": "exact-match"}]},
        ],
        examples=[_ex("a", expected="output")],
    )
    config = EvalRunConfig(
        executor=lambda s, ex: "output",
        judge_fn=lambda prompt: json.dumps({"accuracy": 0.9}),
    )
    result = EvalRunner(suite, config).run()
    scored = result.examples[0]
    assert abs(scored.score - 0.95) < 1e-9
    assert len(scored.evaluator_results) == 2
