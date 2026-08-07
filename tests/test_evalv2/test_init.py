"""Tests for evalv2 init — bootstrapping an eval/ suite into a skill."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nuvel.evalv2 import cli
from nuvel.evalv2.init import default_suite_yaml, init_eval_suite


def _make_skill(tmp_path: Path, name: str = "my-skill") -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill", encoding="utf-8")
    return skill_dir


# --------------------------------------------------------------------------- #
# init_eval_suite
# --------------------------------------------------------------------------- #
def test_init_creates_suite_and_examples(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    eval_dir = init_eval_suite(skill_dir)

    assert eval_dir == skill_dir / "eval"
    assert (eval_dir / "suite.yaml").is_file()
    assert (eval_dir / "examples").is_dir()
    assert (eval_dir / "examples" / ".gitkeep").is_file()


def test_default_name_is_skill_name_eval(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path, "summarize")
    eval_dir = init_eval_suite(skill_dir)
    data = yaml.safe_load((eval_dir / "suite.yaml").read_text(encoding="utf-8"))
    assert data["name"] == "summarize-eval"
    assert data["skill"] == "summarize"


def test_explicit_name_overrides_default(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path, "summarize")
    eval_dir = init_eval_suite(skill_dir, name="custom")
    data = yaml.safe_load((eval_dir / "suite.yaml").read_text(encoding="utf-8"))
    assert data["name"] == "custom-eval"


def test_suite_has_required_structure(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    eval_dir = init_eval_suite(skill_dir)
    data = yaml.safe_load((eval_dir / "suite.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    for key in ("name", "description", "skill", "evaluators", "thresholds"):
        assert key in data
    assert isinstance(data["evaluators"], list)
    assert isinstance(data["thresholds"], dict)


def test_suite_loads_via_evalsuite(tmp_path: Path) -> None:
    from nuvel.evalv2.suite import EvalSuite

    skill_dir = _make_skill(tmp_path, "summarize")
    init_eval_suite(skill_dir)
    suite = EvalSuite.from_skill(skill_dir)
    assert suite.name == "summarize-eval"
    assert suite.evaluators  # non-empty


def test_custom_description(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    eval_dir = init_eval_suite(skill_dir, description="A bespoke suite.")
    data = yaml.safe_load((eval_dir / "suite.yaml").read_text(encoding="utf-8"))
    assert data["description"] == "A bespoke suite."


def test_force_overwrites_existing(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    init_eval_suite(skill_dir)
    # Mutate the file, then re-init with force.
    (skill_dir / "eval" / "suite.yaml").write_text("stale: true\n", encoding="utf-8")
    init_eval_suite(skill_dir, force=True)
    data = yaml.safe_load((skill_dir / "eval" / "suite.yaml").read_text(encoding="utf-8"))
    assert "name" in data and "stale" not in data


def test_no_force_raises_on_existing(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    init_eval_suite(skill_dir)
    with pytest.raises(FileExistsError):
        init_eval_suite(skill_dir)


def test_missing_skill_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        init_eval_suite(tmp_path / "does-not-exist")


def test_default_suite_yaml_parses(tmp_path: Path) -> None:
    data = yaml.safe_load(default_suite_yaml("demo"))
    assert data["name"] == "demo-eval"
    assert data["skill"] == "demo"


# --------------------------------------------------------------------------- #
# CLI: nuvel evalv2 init
# --------------------------------------------------------------------------- #
def test_cli_init_by_path(tmp_path: Path, capsys) -> None:
    skill_dir = _make_skill(tmp_path)
    rc = cli.init_eval(str(skill_dir), skills_dir=tmp_path)
    assert rc == 0
    assert (skill_dir / "eval" / "suite.yaml").is_file()


def test_cli_init_by_name_under_skills_dir(tmp_path: Path) -> None:
    _make_skill(tmp_path, "widget")
    rc = cli.init_eval("widget", skills_dir=tmp_path)
    assert rc == 0
    assert (tmp_path / "widget" / "eval" / "suite.yaml").is_file()


def test_cli_init_missing_skill_returns_1(tmp_path: Path) -> None:
    rc = cli.init_eval("nope", skills_dir=tmp_path)
    assert rc == 1


def test_cli_init_existing_returns_1_without_force(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    cli.init_eval(str(skill_dir), skills_dir=tmp_path)
    rc = cli.init_eval(str(skill_dir), skills_dir=tmp_path)
    assert rc == 1


def test_cli_init_force_succeeds(tmp_path: Path) -> None:
    skill_dir = _make_skill(tmp_path)
    cli.init_eval(str(skill_dir), skills_dir=tmp_path)
    rc = cli.init_eval(str(skill_dir), skills_dir=tmp_path, force=True)
    assert rc == 0
