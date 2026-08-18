"""Tests for the evalv2 CLI — tmp fixtures, fake executor, no network."""
from __future__ import annotations

import argparse
import json

import pytest

from nuvel.evalv2 import cli


def _fake_executor(_suite, _example) -> str:
    return "A short summary."


def _fake_judge(_prompt) -> str:
    return json.dumps({"accuracy": 0.95, "conciseness": 0.95, "tone": 0.95})


def _low_judge(_prompt) -> str:
    return json.dumps({"accuracy": 0.3, "conciseness": 0.3, "tone": 0.3})


# --------------------------------------------------------------------------- #
# get_results_dir
# --------------------------------------------------------------------------- #
def test_get_results_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert cli.get_results_dir() == tmp_path / "nuvel" / "evalv2"


def test_get_results_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.get_results_dir() == tmp_path / ".local" / "share" / "nuvel" / "evalv2"


# --------------------------------------------------------------------------- #
# find_eval_skills / list
# --------------------------------------------------------------------------- #
def test_find_eval_skills_discovers_suite(summarize_skill_dir):
    skills_dir = summarize_skill_dir.parent
    found = cli.find_eval_skills(skills_dir)
    by_name = {f["skill"]: f for f in found}
    assert "summarize-skill" in by_name
    assert "no-eval-skill" not in by_name  # has no eval/ dir
    rec = by_name["summarize-skill"]
    assert rec["suite"] == "summarize-eval"
    assert rec["examples"] >= 1
    assert "llm-judge" in rec["evaluators"]


def test_list_empty_dir(tmp_path, capsys):
    args = argparse.Namespace(skills_dir=str(tmp_path))
    assert cli._cmd_list(args) == 0
    assert "No skills with eval suites found." in capsys.readouterr().out


def test_list_shows_skill(summarize_skill_dir, capsys):
    args = argparse.Namespace(skills_dir=str(summarize_skill_dir.parent))
    assert cli._cmd_list(args) == 0
    out = capsys.readouterr().out
    assert "summarize-skill" in out
    assert "summarize-eval" in out


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def test_run_missing_eval_returns_1(no_eval_skill_dir, tmp_path, capsys):
    code = cli.run_eval(
        no_eval_skill_dir.name,
        skills_dir=no_eval_skill_dir.parent,
        results_dir=tmp_path,
    )
    assert code == 1
    assert "no eval/suite.yaml" in capsys.readouterr().err


def test_run_json_emits_valid_json(summarize_skill_dir, tmp_path, capsys):
    code = cli.run_eval(
        "summarize-skill",
        skills_dir=summarize_skill_dir.parent,
        results_dir=tmp_path,
        json_out=True,
        executor=_fake_executor,
        judge_fn=_fake_judge,
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"] == "summarize"
    assert payload["schema_version"]
    assert payload["summary"]["total"] >= 1


def test_run_persists_result_and_baseline(summarize_skill_dir, tmp_path):
    cli.run_eval(
        "summarize-skill",
        skills_dir=summarize_skill_dir.parent,
        results_dir=tmp_path,
        save_baseline=True,
        executor=_fake_executor,
        judge_fn=_fake_judge,
    )
    skill_results = tmp_path / "summarize-skill"
    assert (skill_results / "baseline.json").is_file()
    runs = [p for p in skill_results.glob("*.json") if p.name != "baseline.json"]
    assert len(runs) == 1


# --------------------------------------------------------------------------- #
# compare
# --------------------------------------------------------------------------- #
def test_compare_missing_baseline_returns_1(tmp_path, capsys):
    code = cli.compare_eval("summarize-skill", results_dir=tmp_path)
    assert code == 1
    assert "no baseline" in capsys.readouterr().err


def test_compare_regression_exits_2(summarize_skill_dir, tmp_path):
    skills_dir = summarize_skill_dir.parent
    # baseline: strong scores
    cli.run_eval(
        "summarize-skill",
        skills_dir=skills_dir,
        results_dir=tmp_path,
        save_baseline=True,
        executor=_fake_executor,
        judge_fn=_fake_judge,
    )
    # current: weak scores -> regression
    cli.run_eval(
        "summarize-skill",
        skills_dir=skills_dir,
        results_dir=tmp_path,
        executor=_fake_executor,
        judge_fn=_low_judge,
    )
    code = cli.compare_eval("summarize-skill", results_dir=tmp_path)
    assert code == 2


def test_compare_json_output(summarize_skill_dir, tmp_path, capsys):
    skills_dir = summarize_skill_dir.parent
    cli.run_eval(
        "summarize-skill",
        skills_dir=skills_dir,
        results_dir=tmp_path,
        save_baseline=True,
        executor=_fake_executor,
        judge_fn=_fake_judge,
    )
    cli.run_eval(
        "summarize-skill",
        skills_dir=skills_dir,
        results_dir=tmp_path,
        executor=_fake_executor,
        judge_fn=_fake_judge,
    )
    capsys.readouterr()  # flush the two run reports
    code = cli.compare_eval("summarize-skill", results_dir=tmp_path, json_out=True)
    out = capsys.readouterr().out
    report = json.loads(out)
    assert "summary" in report
    assert "regressed" in report
    assert code in (0, 2)


# --------------------------------------------------------------------------- #
# top-level CLI wiring
# --------------------------------------------------------------------------- #
def test_registered_on_top_level_parser(tmp_path, capsys):
    from nuvel.cli import main

    code = main(["evalv2", "list", "--skills-dir", str(tmp_path)])
    assert code == 0
    assert "No skills with eval suites found." in capsys.readouterr().out
