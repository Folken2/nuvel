"""Tests for nuvel.bots.fleet — the YAML-manifest fleet deployer + its CLI.

The deployer's collaborators (``BotClient`` / ``SkillManager``) are mocked, so
no real hermes CLI or skills hub is touched. Fleet tracking files are written
under a ``tmp_path``-scoped hermes home. CLI tests drive argparse → dispatch
directly with ``FleetDeployer`` mocked at the module level.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from nuvel.bots.errors import BotError, BotNotFoundError, FleetError
from nuvel.bots.fleet import (
    BotDeployResult,
    FleetDeployer,
    FleetDeployResult,
)
from nuvel.bots.types import InstalledSkill


MANIFEST = """\
name: acme-support
company: Acme Corp
default_model: deepseek/deepseek-v4-flash
bots:
  - name: triage-bot
    role: Customer Support Triage
    description: Classifies and routes incoming customer requests
    skills:
      - customer/triage-agent
  - name: payroll-bot
    role: Payroll Processor
    description: Processes employee timesheets
    model: anthropic/claude-sonnet-4
    skills:
      - hr/payroll-processor
"""


def _write_manifest(tmp_path, text=MANIFEST):
    path = tmp_path / "fleet.yaml"
    path.write_text(text)
    return str(path)


def _deployer(tmp_path):
    """A FleetDeployer whose client + skill manager are MagicMocks."""
    d = FleetDeployer(hermes_bin="/fake/hermes", hermes_home=str(tmp_path))
    d._client = MagicMock()
    d._client.list_bots.return_value = []  # nothing exists yet by default
    d._client.create_cron_job.return_value = "job123"
    d._skill_mgr = MagicMock()
    d._skill_mgr.install_skills.return_value = []
    return d


# Governance manifests: vision (inline/file), a manager bot, and bot routines.
VISION_MD = "# Fleet Constitution\nAll bots follow these rules.\n"

MANIFEST_VISION_INLINE = f"""\
name: celo-fixings
company: Celo Fixings Ltd
vision: |
  {VISION_MD.replace(chr(10), chr(10) + "  ").rstrip()}
bots:
  - name: payroll-bot
    role: Payroll Processor
"""

MANIFEST_MANAGER = """\
name: celo-fixings
company: Celo Fixings Ltd
manager:
  name: manager-bot
  model: anthropic/claude-sonnet-4
  description: Fleet supervisor
bots:
  - name: payroll-bot
    role: Payroll Processor
  - name: triage-bot
    role: Triage
"""

MANIFEST_ROUTINES = """\
name: celo-fixings
company: Celo Fixings Ltd
bots:
  - name: payroll-bot
    role: Payroll Processor
    routines:
      - schedule: "0 8 * * 1-5"
        task: "Process yesterday's timesheets and report discrepancies"
      - schedule: "0 9 * * 1"
        task: "Weekly summary"
"""


# --------------------------------------------------------------------------- #
# 1. manifest parsing
# --------------------------------------------------------------------------- #
def test_deploy_manifest_parsing(tmp_path):
    path = _write_manifest(tmp_path)
    manifest = _deployer(tmp_path)._load_manifest(path)
    assert manifest.name == "acme-support"
    assert manifest.company == "Acme Corp"
    assert manifest.default_model == "deepseek/deepseek-v4-flash"
    assert [b["name"] for b in manifest.bots] == ["triage-bot", "payroll-bot"]


# --------------------------------------------------------------------------- #
# 2. deploy — creation, skills, default model
# --------------------------------------------------------------------------- #
def test_deploy_creates_bots(tmp_path):
    d = _deployer(tmp_path)
    result = d.deploy(_write_manifest(tmp_path))

    assert isinstance(result, FleetDeployResult)
    assert result.fleet_name == "acme-support"
    assert result.success is True
    assert [b.status for b in result.bots] == ["created", "created"]

    calls = {c.args[0]: c.kwargs for c in d._client.create_bot.call_args_list}
    assert set(calls) == {"triage-bot", "payroll-bot"}
    # triage-bot has no explicit model → inherits the fleet default
    assert calls["triage-bot"]["model"] == "deepseek/deepseek-v4-flash"
    # payroll-bot's own model wins over the default
    assert calls["payroll-bot"]["model"] == "anthropic/claude-sonnet-4"
    assert calls["triage-bot"]["description"] == "Classifies and routes incoming customer requests"


def test_deploy_installs_skills(tmp_path):
    d = _deployer(tmp_path)
    d._skill_mgr.install_skills.side_effect = lambda name, refs, hermes_home=None: [
        InstalledSkill(name=ref.split("/")[-1], category=ref.split("/")[0], path=f"/x/{ref}")
        for ref in refs
    ]
    result = d.deploy(_write_manifest(tmp_path))

    installed = {c.args[0]: c.args[1] for c in d._skill_mgr.install_skills.call_args_list}
    assert installed["triage-bot"] == ["customer/triage-agent"]
    assert installed["payroll-bot"] == ["hr/payroll-processor"]
    by_name = {b.name: b for b in result.bots}
    assert by_name["triage-bot"].skills_installed == ["customer/triage-agent"]
    assert by_name["payroll-bot"].skills_installed == ["hr/payroll-processor"]


def test_deploy_idempotent(tmp_path):
    d = _deployer(tmp_path)
    existing = MagicMock()
    existing.name = "triage-bot"
    d._client.list_bots.return_value = [existing]

    result = d.deploy(_write_manifest(tmp_path))
    by_name = {b.name: b for b in result.bots}
    assert by_name["triage-bot"].status == "already_exists"
    assert by_name["payroll-bot"].status == "created"
    # the pre-existing bot is never re-created
    created = [c.args[0] for c in d._client.create_bot.call_args_list]
    assert created == ["payroll-bot"]


def test_deploy_partial_failure(tmp_path):
    d = _deployer(tmp_path)

    def create(name, **kwargs):
        if name == "payroll-bot":
            raise BotError("hermes exploded")
        return MagicMock()

    d._client.create_bot.side_effect = create
    result = d.deploy(_write_manifest(tmp_path))

    by_name = {b.name: b for b in result.bots}
    assert by_name["triage-bot"].status == "created"
    assert by_name["payroll-bot"].status == "failed"
    assert "hermes exploded" in by_name["payroll-bot"].error
    assert result.success is False  # fleet is unhealthy if any bot failed


# --------------------------------------------------------------------------- #
# 3. tracking file — status / list / destroy
# --------------------------------------------------------------------------- #
def test_status(tmp_path):
    d = _deployer(tmp_path)
    d.deploy(_write_manifest(tmp_path))  # writes the tracking file

    status = d.status("acme-support")
    assert status is not None
    assert status.fleet_name == "acme-support"
    assert status.company == "Acme Corp"
    assert {b.name for b in status.bots} == {"triage-bot", "payroll-bot"}
    assert isinstance(status.started_at, datetime)

    assert d.status("no-such-fleet") is None


def test_list_fleets(tmp_path):
    d = _deployer(tmp_path)
    assert d.list_fleets() == []
    d.deploy(_write_manifest(tmp_path))
    d.deploy(_write_manifest(tmp_path, MANIFEST.replace("acme-support", "beta-fleet")))
    assert d.list_fleets() == ["acme-support", "beta-fleet"]


def test_destroy(tmp_path):
    d = _deployer(tmp_path)
    d.deploy(_write_manifest(tmp_path))
    assert d.status("acme-support") is not None

    d.destroy(_write_manifest(tmp_path))
    deleted = [c.args[0] for c in d._client.delete_bot.call_args_list]
    assert deleted == ["triage-bot", "payroll-bot"]
    # tracking file is removed on teardown
    assert d.status("acme-support") is None


def test_destroy_tolerates_missing_bot(tmp_path):
    d = _deployer(tmp_path)
    d.deploy(_write_manifest(tmp_path))
    d._client.delete_bot.side_effect = BotNotFoundError("already gone")
    d.destroy(_write_manifest(tmp_path))  # must not raise


# --------------------------------------------------------------------------- #
# 3b. governance — vision, manager, routines
# --------------------------------------------------------------------------- #
def test_deploy_with_vision_inline(tmp_path):
    d = _deployer(tmp_path)
    result = d.deploy(_write_manifest(tmp_path, MANIFEST_VISION_INLINE))

    assert result.has_vision is True
    vision_file = tmp_path / "fleets" / "celo-fixings" / "VISION.md"
    assert vision_file.is_file()
    text = vision_file.read_text()
    assert text.startswith("# Fleet Constitution")
    assert "All bots follow these rules." in text
    assert result.vision_path == str(vision_file)


def test_deploy_with_vision_file(tmp_path):
    vision_src = tmp_path / "constitution.md"
    vision_src.write_text("# From a file\nHello.\n")
    manifest = (
        "name: celo-fixings\ncompany: Celo Fixings Ltd\n"
        "vision: constitution.md\n"
        "bots:\n  - name: payroll-bot\n    role: Payroll\n"
    )
    d = _deployer(tmp_path)
    result = d.deploy(_write_manifest(tmp_path, manifest))

    assert result.has_vision is True
    vision_file = tmp_path / "fleets" / "celo-fixings" / "VISION.md"
    assert vision_file.read_text() == "# From a file\nHello.\n"


def test_vision_symlinks_created(tmp_path):
    d = _deployer(tmp_path)
    d.deploy(_write_manifest(tmp_path, MANIFEST_VISION_INLINE))

    link = tmp_path / "profiles" / "payroll-bot" / "VISION.md"
    assert link.is_symlink()
    assert link.resolve() == (tmp_path / "fleets" / "celo-fixings" / "VISION.md").resolve()
    assert link.read_text().startswith("# Fleet Constitution")


def test_deploy_with_manager(tmp_path):
    d = _deployer(tmp_path)
    result = d.deploy(_write_manifest(tmp_path, MANIFEST_MANAGER))

    assert result.manager == "manager-bot"
    created = {c.args[0]: c.kwargs for c in d._client.create_bot.call_args_list}
    assert "manager-bot" in created
    assert created["manager-bot"]["model"] == "anthropic/claude-sonnet-4"

    # fleet-management skills are installed on the manager
    installs = {c.args[0]: c.args[1] for c in d._skill_mgr.install_skills.call_args_list}
    assert installs["manager-bot"] == ["fleet/manage", "fleet/troubleshoot"]

    # SOUL.md describes the fleet + its topology
    soul = (tmp_path / "profiles" / "manager-bot" / "SOUL.md").read_text()
    assert "manager of the celo-fixings fleet" in soul
    assert "payroll-bot" in soul and "triage-bot" in soul
    assert "## Fleet Topology" in soul


def test_deploy_with_routines(tmp_path):
    d = _deployer(tmp_path)
    d._client.create_cron_job.side_effect = ["job-a", "job-b"]
    result = d.deploy(_write_manifest(tmp_path, MANIFEST_ROUTINES))

    calls = d._client.create_cron_job.call_args_list
    assert len(calls) == 2
    # positional: (bot, schedule, task)
    assert calls[0].args[0] == "payroll-bot"
    assert calls[0].args[1] == "0 8 * * 1-5"
    assert "timesheets" in calls[0].args[2]

    assert [r["job_id"] for r in result.routines] == ["job-a", "job-b"]
    assert all(r["bot"] == "payroll-bot" for r in result.routines)


def test_routines_survive_cron_failure(tmp_path):
    """A cron backend that rejects the call is recorded, not fatal."""
    d = _deployer(tmp_path)
    d._client.create_cron_job.side_effect = BotError("cron unsupported")
    result = d.deploy(_write_manifest(tmp_path, MANIFEST_ROUTINES))

    assert result.success is True  # the bot still deployed
    assert all(r["job_id"] is None for r in result.routines)
    assert all("cron unsupported" in r["error"] for r in result.routines)


def test_update_vision(tmp_path):
    d = _deployer(tmp_path)
    d.deploy(_write_manifest(tmp_path, MANIFEST_VISION_INLINE))

    d.update_vision("celo-fixings", "# New Rules\nUpdated.\n")

    vision_file = tmp_path / "fleets" / "celo-fixings" / "VISION.md"
    assert vision_file.read_text() == "# New Rules\nUpdated.\n"
    link = tmp_path / "profiles" / "payroll-bot" / "VISION.md"
    assert link.is_symlink()
    assert link.read_text() == "# New Rules\nUpdated.\n"

    # tracking file reflects the refreshed vision
    status = d.status("celo-fixings")
    assert status.has_vision is True
    assert status.vision_path == str(vision_file)


def test_update_vision_unknown_fleet(tmp_path):
    d = _deployer(tmp_path)
    with pytest.raises(FleetError):
        d.update_vision("ghost", "# x\n")


def test_fleet_status_with_governance(tmp_path):
    d = _deployer(tmp_path)
    manifest = (
        "name: celo-fixings\ncompany: Celo Fixings Ltd\n"
        "vision: |\n  # Rules\n  Be good.\n"
        "manager:\n  name: manager-bot\n"
        "bots:\n"
        "  - name: payroll-bot\n    role: Payroll\n"
        "    routines:\n      - schedule: \"0 8 * * 1-5\"\n        task: \"Do payroll\"\n"
    )
    d.deploy(_write_manifest(tmp_path, manifest))

    status = d.status("celo-fixings")
    assert status.has_vision is True
    assert status.manager == "manager-bot"
    assert len(status.routines) == 1
    assert status.routines[0]["bot"] == "payroll-bot"
    assert status.routines[0]["job_id"] == "job123"


def test_destroy_removes_routines_and_manager(tmp_path):
    d = _deployer(tmp_path)
    manifest = (
        "name: celo-fixings\ncompany: Celo Fixings Ltd\n"
        "manager:\n  name: manager-bot\n"
        "bots:\n"
        "  - name: payroll-bot\n    role: Payroll\n"
        "    routines:\n      - schedule: \"0 8 * * 1-5\"\n        task: \"Do payroll\"\n"
    )
    path = _write_manifest(tmp_path, manifest)
    d.deploy(path)

    d.destroy(path)

    d._client.remove_cron_job.assert_called_once_with("payroll-bot", "job123")
    deleted = [c.args[0] for c in d._client.delete_bot.call_args_list]
    assert "payroll-bot" in deleted and "manager-bot" in deleted
    assert d.status("celo-fixings") is None


# --------------------------------------------------------------------------- #
# 4. CLI — argparse + dispatch (FleetDeployer mocked)
# --------------------------------------------------------------------------- #
from nuvel.bots.cli import (  # noqa: E402
    _cmd_fleet_deploy,
    _cmd_fleet_destroy,
    _cmd_fleet_list,
    _cmd_fleet_status,
    _cmd_fleet_update_vision,
    _dispatch,
    register_fleet,
)


@pytest.fixture
def parser():
    parent = argparse.ArgumentParser()
    sub = parent.add_subparsers()
    register_fleet(sub)
    return parent


def _result(name="acme-support", success=True):
    now = datetime.now()
    return FleetDeployResult(
        fleet_name=name,
        bots=[BotDeployResult(name="triage-bot", status="created", skills_installed=[], error=None)],
        success=success,
        started_at=now,
        completed_at=now,
    )


class TestFleetArgparse:
    @pytest.mark.parametrize(
        "argv, command, func",
        [
            (["fleet", "deploy", "f.yaml"], "deploy", _cmd_fleet_deploy),
            (["fleet", "list"], "list", _cmd_fleet_list),
            (["fleet", "status", "acme"], "status", _cmd_fleet_status),
            (["fleet", "destroy", "f.yaml"], "destroy", _cmd_fleet_destroy),
            (
                ["fleet", "update-vision", "acme", "# x"],
                "update-vision",
                _cmd_fleet_update_vision,
            ),
        ],
    )
    def test_subcommand_parses(self, parser, argv, command, func):
        args = parser.parse_args(argv)
        assert args.fleet_command == command
        assert args._bots_func is func
        assert args.func is _dispatch

    def test_requires_subcommand(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["fleet"])


class TestFleetCliDeploy:
    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_deploy(self, mock_dep, parser, capsys):
        mock_dep.return_value.deploy.return_value = _result()
        args = parser.parse_args(["fleet", "deploy", "f.yaml", "--hermes-bin", "/x/hermes"])
        assert _dispatch(args) == 0
        mock_dep.assert_called_once_with(hermes_bin="/x/hermes")
        mock_dep.return_value.deploy.assert_called_once_with("f.yaml")
        assert "acme-support" in capsys.readouterr().out

    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_deploy_failure_exit_1(self, mock_dep, parser):
        mock_dep.return_value.deploy.return_value = _result(success=False)
        args = parser.parse_args(["fleet", "deploy", "f.yaml"])
        assert _dispatch(args) == 1

    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_deploy_json(self, mock_dep, parser, capsys):
        mock_dep.return_value.deploy.return_value = _result()
        args = parser.parse_args(["fleet", "deploy", "f.yaml", "--json"])
        assert _dispatch(args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["fleet_name"] == "acme-support"


class TestFleetCliList:
    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_list(self, mock_dep, parser, capsys):
        mock_dep.return_value.list_fleets.return_value = ["acme-support", "beta"]
        args = parser.parse_args(["fleet", "list"])
        assert _dispatch(args) == 0
        out = capsys.readouterr().out
        assert "acme-support" in out and "beta" in out

    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_list_empty(self, mock_dep, parser, capsys):
        mock_dep.return_value.list_fleets.return_value = []
        args = parser.parse_args(["fleet", "list"])
        assert _dispatch(args) == 0
        assert "No fleets" in capsys.readouterr().out


class TestFleetCliStatus:
    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_status(self, mock_dep, parser, capsys):
        from nuvel.bots.fleet import FleetStatus

        now = datetime.now()
        mock_dep.return_value.status.return_value = FleetStatus(
            fleet_name="acme-support",
            company="Acme Corp",
            bots=[BotDeployResult(name="triage-bot", status="created", skills_installed=[], error=None)],
            success=True,
            started_at=now,
            completed_at=now,
        )
        args = parser.parse_args(["fleet", "status", "acme-support"])
        assert _dispatch(args) == 0
        out = capsys.readouterr().out
        assert "acme-support" in out and "triage-bot" in out

    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_status_missing(self, mock_dep, parser, capsys):
        mock_dep.return_value.status.return_value = None
        args = parser.parse_args(["fleet", "status", "ghost"])
        assert _dispatch(args) == 1
        assert "ghost" in capsys.readouterr().out


class TestFleetCliDestroy:
    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_destroy(self, mock_dep, parser, capsys):
        args = parser.parse_args(["fleet", "destroy", "f.yaml"])
        assert _dispatch(args) == 0
        mock_dep.return_value.destroy.assert_called_once_with("f.yaml")
        assert "destroyed" in capsys.readouterr().out.lower()


class TestFleetCliUpdateVision:
    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_update_vision(self, mock_dep, parser, capsys):
        args = parser.parse_args(["fleet", "update-vision", "acme", "# Rules"])
        assert _dispatch(args) == 0
        mock_dep.return_value.update_vision.assert_called_once_with("acme", "# Rules")
        assert "acme" in capsys.readouterr().out

    @patch("nuvel.bots.cli.FleetDeployer")
    def test_cli_update_vision_unknown(self, mock_dep, parser, capsys):
        mock_dep.return_value.update_vision.side_effect = FleetError("no fleet named 'ghost'")
        args = parser.parse_args(["fleet", "update-vision", "ghost", "# x"])
        assert _dispatch(args) == 1
