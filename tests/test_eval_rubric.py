"""Rubric loading and model resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nuvel._defaults import DEFAULT_FAST_MODEL
from nuvel.eval.rubric import (
    DEFAULT_RUBRIC,
    DEFAULT_RUBRIC_VERSION,
    Rubric,
    load_rubric,
)


def test_default_rubric_has_expected_weights() -> None:
    assert DEFAULT_RUBRIC.version == DEFAULT_RUBRIC_VERSION
    assert sum(DEFAULT_RUBRIC.weights.values()) == pytest.approx(1.0)
    assert set(DEFAULT_RUBRIC.weights.keys()) == {
        "success", "quality", "efficiency", "reliability"
    }


def test_resolved_model_priority_rubric_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "env-model")
    r = Rubric(judge_model="rubric-model")
    assert r.resolved_model() == "rubric-model"


def test_resolved_model_priority_env_beats_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "env-model")
    assert Rubric().resolved_model() == "env-model"


def test_resolved_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    assert Rubric().resolved_model() == DEFAULT_FAST_MODEL


def test_load_rubric_missing_returns_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_rubric("no-such-agent") is DEFAULT_RUBRIC


def test_load_rubric_strips_subagent_suffix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = "outlook-king"
    (tmp_path / "generated-agents" / agent / "evals").mkdir(parents=True)
    (tmp_path / "generated-agents" / agent / "evals" / "rubric.yaml").write_text(
        "version: outlook-king-1.0\n"
        "weights: {success: 0.5, quality: 0.3, efficiency: 0.1, reliability: 0.1}\n"
        "judge: {model: anthropic/claude-haiku-4-5}\n"
    )
    monkeypatch.chdir(tmp_path)
    r = load_rubric("outlook-king/composer")
    assert r.version == "outlook-king-1.0"
    assert r.judge_model == "anthropic/claude-haiku-4-5"


def test_load_rubric_extra_criteria(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = "x"
    (tmp_path / "generated-agents" / agent / "evals").mkdir(parents=True)
    (tmp_path / "generated-agents" / agent / "evals" / "rubric.yaml").write_text(
        'version: x-1\nextra_criteria: "Watch for tone mismatch."\n'
    )
    monkeypatch.chdir(tmp_path)
    r = load_rubric(agent)
    assert "tone mismatch" in r.extra_criteria


def test_load_rubric_malformed_yaml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = "x"
    (tmp_path / "generated-agents" / agent / "evals").mkdir(parents=True)
    (tmp_path / "generated-agents" / agent / "evals" / "rubric.yaml").write_text(
        "not: valid: yaml: [unclosed\n"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="YAML parse error"):
        load_rubric(agent)


def test_load_rubric_root_must_be_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = "x"
    (tmp_path / "generated-agents" / agent / "evals").mkdir(parents=True)
    (tmp_path / "generated-agents" / agent / "evals" / "rubric.yaml").write_text(
        "- just\n- a\n- list\n"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="mapping"):
        load_rubric(agent)
