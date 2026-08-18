"""Wire-format round-trip and serialization tests."""
from __future__ import annotations

import json

from nuvel.evalv2.schema import (
    SCHEMA_VERSION,
    EvalSummary,
    EvalSuiteResult,
    EvaluatorResult,
    ScoredExample,
)


def test_scored_example_roundtrip():
    example = ScoredExample(
        id="ex-1",
        input="summarize this",
        score=0.87,
        passed=True,
        evaluator_results=[
            EvaluatorResult(
                evaluator="llm-judge",
                name="rubric",
                score=0.9,
                passed=True,
                details={"accuracy": 0.95, "tone": 0.85},
            ),
            EvaluatorResult(
                evaluator="deterministic",
                name="max-length",
                score=None,
                passed=True,
            ),
        ],
        cache_hit=True,
        cost=0.0123,
        notes=["looks good"],
    )
    restored = ScoredExample.from_dict(example.to_dict())
    assert restored == example


def test_scored_example_minimal_roundtrip():
    example = ScoredExample(id="ex-2", input="hello")
    restored = ScoredExample.from_dict(example.to_dict())
    assert restored == example
    assert restored.score is None
    assert restored.passed is None
    assert restored.evaluator_results == []


def test_suite_result_to_dict_structure():
    result = EvalSuiteResult(
        schema_version=SCHEMA_VERSION,
        skill="summarize",
        suite="summarize-eval",
        timestamp="2026-08-07T10:00:00Z",
        model="openai/gpt-4o-mini",
        summary=EvalSummary(total=3, passed=2, warn=1, overall=0.82),
        examples=[ScoredExample(id="ex-1", input="a", score=0.9, passed=True)],
        flags=[{"id": "ex-1", "flag": "cost_outlier"}],
        cost={"total": 0.05},
    )
    d = result.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["skill"] == "summarize"
    assert d["summary"]["total"] == 3
    assert d["summary"]["overall"] == 0.82
    assert isinstance(d["examples"], list)
    assert d["examples"][0]["id"] == "ex-1"
    assert d["flags"][0]["flag"] == "cost_outlier"
    assert d["cost"]["total"] == 0.05


def test_suite_result_to_json_is_valid():
    result = EvalSuiteResult(
        schema_version=SCHEMA_VERSION,
        skill="summarize",
        suite="summarize-eval",
        timestamp="2026-08-07T10:00:00Z",
        examples=[ScoredExample(id="ex-1", input="a")],
    )
    text = result.to_json()
    parsed = json.loads(text)
    assert parsed["schema_version"] == SCHEMA_VERSION
    # indent=2 => multi-line pretty output
    assert "\n" in text


def test_suite_result_roundtrip():
    result = EvalSuiteResult(
        schema_version=SCHEMA_VERSION,
        skill="summarize",
        suite="summarize-eval",
        timestamp="2026-08-07T10:00:00Z",
        model="openai/gpt-4o-mini",
        summary=EvalSummary(total=1, passed=1, overall=0.9),
        examples=[ScoredExample(id="ex-1", input="a", score=0.9, passed=True)],
    )
    restored = EvalSuiteResult.from_dict(result.to_dict())
    assert restored == result


def test_empty_suite_result_serializes():
    result = EvalSuiteResult(
        schema_version=SCHEMA_VERSION,
        skill="",
        suite="",
        timestamp="2026-08-07T10:00:00Z",
    )
    d = result.to_dict()
    assert d["examples"] == []
    assert d["flags"] == []
    assert d["cost"] == {}
    assert d["summary"]["total"] == 0
    assert d["summary"]["overall"] is None
    # round-trips too
    assert EvalSuiteResult.from_dict(d) == result
