"""EvalSuite loading and example discovery tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nuvel.evalv2.exceptions import ExampleError, SuiteError
from nuvel.evalv2.suite import EvalSuite


def test_from_skill_loads_suite_and_examples(summarize_skill_dir: Path):
    suite = EvalSuite.from_skill(summarize_skill_dir)
    assert suite.name == "summarize-eval"
    assert suite.skill == "summarize"
    assert suite.description == "Evaluates summarize accuracy"
    # happy(1) + edge-cases(2) + regression(1) = 4 examples
    assert len(suite.examples) == 4


def test_from_skill_without_eval_dir_raises(no_eval_skill_dir: Path):
    with pytest.raises(SuiteError):
        EvalSuite.from_skill(no_eval_skill_dir)


def test_suite_yaml_evaluators_and_thresholds(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    assert suite.thresholds == {"pass": 0.8, "warn": 0.6, "regression": -0.05}
    # three evaluator blocks: llm-judge, deterministic, self-consistency
    assert len(suite.evaluators) == 3
    keys = [next(iter(block.keys())) for block in suite.evaluators]
    assert keys == ["llm-judge", "deterministic", "self-consistency"]


def test_single_example_json_discovered(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    ids = {e.id for e in suite.examples}
    assert "happy-01" in ids


def test_array_example_json_discovered(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    ids = {e.id for e in suite.examples}
    assert "edge-empty" in ids
    assert "edge-long" in ids


def test_yaml_example_discovered(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    ids = {e.id for e in suite.examples}
    assert "regression-01" in ids


def test_examples_sorted_by_id(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    ids = [e.id for e in suite.examples]
    assert ids == sorted(ids)


def test_example_fields_parsed(eval_dir: Path):
    suite = EvalSuite.load(eval_dir)
    by_id = {e.id: e for e in suite.examples}
    happy = by_id["happy-01"]
    assert happy.input.startswith("Summarize the quarterly")
    assert happy.tags == ["happy-path", "finance"]
    assert by_id["edge-long"].expected_output is None


def test_missing_suite_yaml_raises(tmp_path: Path):
    (tmp_path / "examples").mkdir()
    with pytest.raises(SuiteError):
        EvalSuite.load(tmp_path)


def test_malformed_example_file_raises(tmp_path: Path):
    eval_dir = tmp_path / "eval"
    (eval_dir / "examples").mkdir(parents=True)
    (eval_dir / "suite.yaml").write_text("name: broken-eval\n", encoding="utf-8")
    (eval_dir / "examples" / "bad.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ExampleError) as exc:
        EvalSuite.load(eval_dir)
    assert "bad.json" in str(exc.value)


def test_example_missing_id_raises(tmp_path: Path):
    eval_dir = tmp_path / "eval"
    (eval_dir / "examples").mkdir(parents=True)
    (eval_dir / "suite.yaml").write_text("name: e\n", encoding="utf-8")
    (eval_dir / "examples" / "noid.json").write_text(
        json.dumps({"input": "x"}), encoding="utf-8"
    )
    with pytest.raises(ExampleError):
        EvalSuite.load(eval_dir)


def test_suite_missing_name_raises(tmp_path: Path):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "suite.yaml").write_text("description: no name here\n", encoding="utf-8")
    with pytest.raises(SuiteError):
        EvalSuite.load(eval_dir)


def test_to_dict(summarize_skill_dir: Path):
    suite = EvalSuite.from_skill(summarize_skill_dir)
    d = suite.to_dict()
    assert d["name"] == "summarize-eval"
    assert len(d["examples"]) == 4
    assert d["root"].endswith("eval")
