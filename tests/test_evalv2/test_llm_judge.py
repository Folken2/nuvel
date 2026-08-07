"""Tests for the evalv2 LLM judge — all model access via the judge_fn seam."""
from __future__ import annotations

import json

from nuvel.evalv2 import Rubric, judge_output
from nuvel.evalv2.suite import EvalExample


def _example() -> EvalExample:
    return EvalExample(id="ex1", input="summarize this", expected_output="a summary")


def test_fake_judge_returns_fixed_json():
    rubric = Rubric(dimensions={"accuracy": 1.0})
    fake = lambda prompt: json.dumps({"accuracy": 0.9})  # noqa: E731
    res = judge_output("out", _example(), rubric, judge_fn=fake)
    assert res.evaluator == "llm-judge"
    assert res.score == 0.9
    assert res.passed is True


def test_weighted_average_normalized():
    rubric = Rubric(dimensions={"accuracy": 3.0, "tone": 1.0})
    fake = lambda prompt: json.dumps({"accuracy": 1.0, "tone": 0.0})  # noqa: E731
    res = judge_output("out", _example(), rubric, judge_fn=fake)
    # (1.0*3 + 0.0*1) / (3+1) == 0.75
    assert abs(res.score - 0.75) < 1e-9


def test_tolerant_parse_with_markdown_fences():
    rubric = Rubric(dimensions={"accuracy": 1.0})
    fenced = "```json\n{\"accuracy\": 0.6}\n```"
    res = judge_output("out", _example(), rubric, judge_fn=lambda p: fenced)
    assert res.score == 0.6


def test_garbage_response_yields_none_score_no_crash():
    rubric = Rubric(dimensions={"accuracy": 1.0})
    res = judge_output("out", _example(), rubric, judge_fn=lambda p: "no json here")
    assert res.score is None
    assert res.passed is None
    assert "parse" in res.details.get("note", "").lower()


def test_passed_threshold_logic():
    rubric = Rubric(dimensions={"accuracy": 1.0})
    low = judge_output("out", _example(), rubric, judge_fn=lambda p: json.dumps({"accuracy": 0.4}))
    assert low.passed is False
    high = judge_output("out", _example(), rubric, judge_fn=lambda p: json.dumps({"accuracy": 0.5}))
    assert high.passed is True


def test_judge_call_failure_captured():
    rubric = Rubric(dimensions={"accuracy": 1.0})

    def boom(prompt):
        raise RuntimeError("network down")

    res = judge_output("out", _example(), rubric, judge_fn=boom)
    assert res.score is None
    assert "failed" in res.details.get("note", "").lower()


def test_rubric_from_config():
    rubric = Rubric.from_config(
        {"rubric": {"accuracy": 0.5, "tone": 0.5}, "model": "openai/gpt-4o-mini", "max_cost": 0.1}
    )
    assert rubric.dimensions == {"accuracy": 0.5, "tone": 0.5}
    assert rubric.model == "openai/gpt-4o-mini"
    assert rubric.max_cost == 0.1
