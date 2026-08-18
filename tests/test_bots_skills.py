"""Tests for :mod:`nuvel.bots.skills` — the skills hub + installer.

A throwaway "hub" is built on disk per-test (``hub`` fixture) so discovery,
listing, search and installation are exercised against real files rather than
mocks. CLI tests drive the argparse wiring in :mod:`nuvel.bots.cli` directly.
"""
from __future__ import annotations

import argparse

import pytest
import yaml

from nuvel.bots.cli import register
from nuvel.bots.errors import SkillNotFoundError
from nuvel.bots.skills import SkillManager
from nuvel.bots.types import InstalledSkill, SkillInfo


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _write_skill(root, category, skill_dir, *, name=None, description="",
                 tags=None, version="1.0.0", scripts=False):
    """Write ``<root>/<category>/<skill_dir>/SKILL.md`` with YAML frontmatter."""
    name = name or skill_dir
    d = root / category / skill_dir
    d.mkdir(parents=True, exist_ok=True)
    fm = {
        "name": name,
        "description": description,
        "version": version,
        "metadata": {"hermes": {"tags": list(tags or [])}},
    }
    body = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# " + name + "\n"
    (d / "SKILL.md").write_text(body)
    if scripts:
        sub = d / "scripts"
        sub.mkdir()
        (sub / "run.sh").write_text("echo hi\n")
    return d


@pytest.fixture
def hub(tmp_path):
    """A miniature skills hub with 6 skills across 2 categories."""
    root = tmp_path / "hub"
    _write_skill(root, "hr", "payroll-processor", description="Process payroll runs",
                 tags=["payroll", "finance"], version="1.0.0", scripts=True)
    _write_skill(root, "hr", "compliance-checker", description="Check HR compliance",
                 tags=["compliance"], version="2.1.0")
    _write_skill(root, "customer", "triage", description="Triage customer tickets",
                 tags=["support"])
    _write_skill(root, "customer", "escalation", description="Escalate issues",
                 tags=["support", "urgent"])
    # An intentionally duplicated flat name across two categories (ambiguous).
    _write_skill(root, "hr", "review", description="HR review", tags=[])
    _write_skill(root, "customer", "review", description="ticket review", tags=[])
    # A hidden/meta dir that must never surface as a category or skill.
    (root / ".hub").mkdir()
    (root / ".hub" / "SKILL.md").write_text("noise\n")
    return root


@pytest.fixture
def mgr(hub):
    return SkillManager(hub_path=str(hub))


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #
def test_list_skills(mgr):
    skills = mgr.list_skills()
    assert all(isinstance(s, SkillInfo) for s in skills)
    assert len(skills) == 6  # hidden .hub/SKILL.md excluded
    payroll = next(s for s in skills if s.name == "payroll-processor")
    assert payroll.category == "hr"
    assert payroll.description == "Process payroll runs"
    assert "payroll" in payroll.tags
    assert payroll.version == "1.0.0"


def test_list_skills_by_category(mgr):
    hr = mgr.list_skills(category="hr")
    assert {s.name for s in hr} == {"payroll-processor", "compliance-checker", "review"}
    assert all(s.category == "hr" for s in hr)


def test_list_categories(mgr):
    assert mgr.list_categories() == ["customer", "hr"]  # sorted, no hidden


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
def test_search_skills(mgr):
    # by name
    assert {s.name for s in mgr.search_skills("payroll")} == {"payroll-processor"}
    # by description
    assert "compliance-checker" in {s.name for s in mgr.search_skills("compliance")}
    # by tag
    assert {s.name for s in mgr.search_skills("finance")} == {"payroll-processor"}
    # case-insensitive
    assert mgr.search_skills("TRIAGE")[0].name == "triage"


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #
def test_install_skills(mgr, tmp_path):
    home = tmp_path / "hermes"
    installed = mgr.install_skills("support", ["hr/payroll-processor"], hermes_home=str(home))
    assert len(installed) == 1
    inst = installed[0]
    assert isinstance(inst, InstalledSkill)
    assert inst.name == "payroll-processor"
    assert inst.category == "hr"
    dest = home / "profiles" / "support" / "skills" / "hr" / "payroll-processor"
    assert (dest / "SKILL.md").exists()
    assert (dest / "scripts" / "run.sh").exists()  # full directory copied
    assert inst.path == str(dest)


def test_install_skills_nonexistent(mgr, tmp_path):
    with pytest.raises(SkillNotFoundError):
        mgr.install_skills("support", ["hr/does-not-exist"], hermes_home=str(tmp_path))


def test_flat_name_resolution(mgr, tmp_path):
    home = tmp_path / "hermes"
    installed = mgr.install_skills("support", ["triage"], hermes_home=str(home))
    assert installed[0].category == "customer"
    assert installed[0].name == "triage"


def test_flat_name_ambiguous(mgr, tmp_path):
    with pytest.raises(SkillNotFoundError) as exc:
        mgr.install_skills("support", ["review"], hermes_home=str(tmp_path))
    msg = str(exc.value)
    assert "hr/review" in msg and "customer/review" in msg


# --------------------------------------------------------------------------- #
# hub auto-discovery
# --------------------------------------------------------------------------- #
def test_hub_auto_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("NUVEL_SKILLS_HUB", raising=False)
    (tmp_path / "skills").mkdir()
    monkeypatch.chdir(tmp_path)
    m = SkillManager()
    from pathlib import Path
    assert Path(m.hub_path).resolve() == (tmp_path / "skills").resolve()


def test_hub_discovery_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("NUVEL_SKILLS_HUB", str(tmp_path / "myhub"))
    (tmp_path / "myhub").mkdir()
    m = SkillManager()
    assert m.hub_path == str(tmp_path / "myhub")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@pytest.fixture
def parser():
    parent = argparse.ArgumentParser()
    sub = parent.add_subparsers()
    register(sub)
    return parent


def test_list_skills_cli(parser, hub, capsys):
    args = parser.parse_args(["bots", "skills", "list", "--hub", str(hub)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "hr" in out and "customer" in out
    assert "payroll-processor" in out
    assert "triage" in out


def test_list_skills_cli_by_category(parser, hub, capsys):
    args = parser.parse_args(
        ["bots", "skills", "list", "--category", "hr", "--hub", str(hub)]
    )
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "payroll-processor" in out
    assert "triage" not in out


def test_search_skills_cli(parser, hub, capsys):
    args = parser.parse_args(["bots", "skills", "search", "payroll", "--hub", str(hub)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "payroll-processor" in out
    assert "escalation" not in out


def test_create_with_skills_cli(parser, capsys):
    from unittest.mock import patch
    from nuvel.bots.types import Bot

    with patch("nuvel.bots.cli.BotClient") as mock_client:
        mock_client.return_value.create_bot_with_skills.return_value = (
            Bot(name="support-bot"),
            [InstalledSkill(name="payroll", category="hr", path="/x/hr/payroll")],
        )
        args = parser.parse_args(
            ["bots", "create", "support-bot", "--skills", "hr/payroll"]
        )
        assert args.func(args) == 0
        mock_client.return_value.create_bot_with_skills.assert_called_once()
        call = mock_client.return_value.create_bot_with_skills.call_args
        assert call.args[0] == "support-bot"
        assert call.args[1] == ["hr/payroll"]
    out = capsys.readouterr().out
    assert "support-bot" in out
    assert "payroll" in out


def test_create_list_skills_cli(parser, hub, capsys):
    """--list-skills prints and exits without creating."""
    from unittest.mock import patch

    with patch("nuvel.bots.cli.BotClient") as mock_client:
        args = parser.parse_args(
            ["bots", "create", "whatever", "--list-skills", "--hub", str(hub)]
        )
        assert args.func(args) == 0
        mock_client.return_value.create_bot.assert_not_called()
        mock_client.return_value.create_bot_with_skills.assert_not_called()
    out = capsys.readouterr().out
    assert "payroll-processor" in out
