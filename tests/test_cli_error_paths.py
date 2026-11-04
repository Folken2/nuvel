"""The failure modes a first user hits, driven through ``nuvel.cli.main``.

Each case asserts the exit code, a one-line message naming the problem, and
that stderr carries no traceback. Network access is blocked for the whole
module (loopback stays open so "provider unreachable" can be a real refused
connection), so nothing here can pass by reaching the internet.
"""
from __future__ import annotations

import errno
import json
import os
import resource
import signal
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from nuvel import pricing
from nuvel.bots import fleet
from nuvel.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect

needs_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Fail every non-loopback lookup and connect the way an offline host does."""

    def getaddrinfo(host, *args, **kwargs):
        if host is None or str(host) in _LOOPBACK:
            return _real_getaddrinfo(host, *args, **kwargs)
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    def connect(self, address):
        if not isinstance(address, tuple) or address[0] in _LOOPBACK:
            return _real_connect(self, address)
        raise OSError(errno.ENETUNREACH, "Network is unreachable")

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Point every state directory the CLI writes at tmp_path."""
    monkeypatch.setattr(fleet, "DEFAULT_HERMES_HOME", tmp_path / ".hermes")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # litellm otherwise fetches its model price map from GitHub on import.
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def _run(argv, capsys):
    rc = main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


def _assert_clean_failure(rc, err, *needles):
    assert rc == 1, err
    assert "Traceback" not in err
    for needle in needles:
        assert needle in err, f"{needle!r} not in stderr: {err!r}"


def _fake_hermes(tmp_path, log: Path | None = None) -> str:
    """A hermes stand-in that succeeds silently, optionally logging its argv."""
    script = tmp_path / "hermes-bin"
    body = f'echo "$@" >> "{log}"\n' if log else ""
    script.write_text(f"#!/bin/sh\n{body}exit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _manifest(tmp_path, text: str) -> str:
    path = tmp_path / "fleet.yaml"
    path.write_text(text)
    return str(path)


def _pricing_file(tmp_path, entries: dict) -> Path:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(entries))
    return path


def _eval_skill(tmp_path, evaluators: str = "[]") -> Path:
    """A skills dir holding one skill, ``demo``, with a one-example suite."""
    skill = tmp_path / "skills" / "demo"
    (skill / "eval" / "examples").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nSay hi.\n")
    (skill / "eval" / "suite.yaml").write_text(f"name: demo-eval\nevaluators: {evaluators}\n")
    (skill / "eval" / "examples" / "one.json").write_text(json.dumps({"id": "one", "input": "hi"}))
    return skill.parent


_SCORED = '[{deterministic: [{type: max-length, max_chars: 50}]}]'


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# --------------------------------------------------------------------------- #
# malformed config file
# --------------------------------------------------------------------------- #
def test_malformed_fleet_manifest(tmp_path, capsys):
    path = _manifest(tmp_path, "name: acme\nbots: [unclosed\n")
    rc, _, err = _run(["fleet", "deploy", path, "--hermes-bin", "/nonexistent"], capsys)
    _assert_clean_failure(rc, err, "invalid YAML", path)


def test_malformed_pricing_file(tmp_path, capsys):
    path = tmp_path / "pricing.json"
    path.write_text('{"openai/gpt-4o": {"input": 1e-06,')
    rc, out, err = _run(["pricing", "list", "-t", str(path)], capsys)
    _assert_clean_failure(rc, err, "not valid JSON", str(path))
    assert out == ""


# --------------------------------------------------------------------------- #
# config with unknown keys
# --------------------------------------------------------------------------- #
def test_fleet_manifest_with_unknown_key(tmp_path, capsys):
    # `bot:` for `bots:` used to deploy an empty fleet and report success.
    path = _manifest(tmp_path, "name: acme\nbot:\n  - name: triage-bot\n")
    rc, _, err = _run(["fleet", "deploy", path, "--hermes-bin", "/nonexistent"], capsys)
    _assert_clean_failure(rc, err, "unknown key(s): bot", path)
    assert not (tmp_path / ".hermes" / "fleets").exists()


# --------------------------------------------------------------------------- #
# missing API key / provider unreachable / no network
# --------------------------------------------------------------------------- #
def test_eval_run_missing_api_key(tmp_path, monkeypatch, capsys):
    pytest.importorskip("litellm")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    skills = _eval_skill(tmp_path, _SCORED)
    rc, out, err = _run(
        ["evalv2", "run", "demo", "--skills-dir", str(skills),
         "--model", "anthropic/claude-sonnet-4"],
        capsys,
    )
    _assert_clean_failure(rc, err, "no API key for model 'anthropic/claude-sonnet-4'",
                          "ANTHROPIC_API_KEY")
    assert "Give Feedback" not in out + err


def test_pricing_sync_provider_unreachable(tmp_path, monkeypatch, capsys):
    url = f"http://127.0.0.1:{_closed_port()}/api/v1/models"
    monkeypatch.setattr(pricing, "OPENROUTER_MODELS_URL", url)
    path = _pricing_file(tmp_path, {"openai/gpt-4o": {"input": 1e-06, "output": 2e-06}})
    before = path.read_text()
    rc, _, err = _run(["pricing", "sync", "-t", str(path)], capsys)
    _assert_clean_failure(rc, err, f"cannot reach {url}", "Connection refused")
    assert path.read_text() == before


def test_pricing_sync_no_network(tmp_path, capsys):
    path = _pricing_file(tmp_path, {"openai/gpt-4o": {"input": 1e-06, "output": 2e-06}})
    rc, _, err = _run(["pricing", "add", "openai/gpt-4o-mini", "-t", str(path)], capsys)
    _assert_clean_failure(rc, err, "cannot reach https://openrouter.ai",
                          "check your network connection")


def test_eval_run_no_network(tmp_path, monkeypatch, capsys):
    pytest.importorskip("litellm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-a-real-key")
    skills = _eval_skill(tmp_path, _SCORED)
    rc, out, err = _run(
        ["evalv2", "run", "demo", "--skills-dir", str(skills),
         "--model", "openrouter/moonshotai/kimi-k2"],
        capsys,
    )
    _assert_clean_failure(rc, err, "cannot reach the provider for model "
                                   "'openrouter/moonshotai/kimi-k2'")
    assert "Give Feedback" not in out + err


# --------------------------------------------------------------------------- #
# unwritable state / config directory
# --------------------------------------------------------------------------- #
@needs_non_root
def test_fleet_deploy_unwritable_state_dir(tmp_path, capsys):
    fleets = tmp_path / ".hermes" / "fleets"
    fleets.mkdir(parents=True)
    fleets.chmod(0o500)
    try:
        path = _manifest(tmp_path, "name: acme\nbots: []\n")
        rc, _, err = _run(["fleet", "deploy", path, "--hermes-bin", _fake_hermes(tmp_path)],
                          capsys)
    finally:
        fleets.chmod(0o700)
    _assert_clean_failure(rc, err, "could not write the record for fleet 'acme'",
                          str(fleets), "Permission denied")


@needs_non_root
def test_eval_run_unwritable_results_dir(tmp_path, capsys):
    skills = _eval_skill(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o500)
    try:
        rc, _, err = _run(["evalv2", "run", "demo", "--skills-dir", str(skills)], capsys)
    finally:
        data.chmod(0o700)
    # The sample cache, written first, is what trips over the permissions.
    _assert_clean_failure(rc, err, str(data), "Permission denied")


@needs_non_root
def test_pricing_sync_unwritable_config_dir(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "conf"
    conf.mkdir()
    path = _pricing_file(conf, {"openai/gpt-4o": {"input": 1e-06, "output": 2e-06}})
    path.chmod(0o400)
    conf.chmod(0o500)
    monkeypatch.setattr(
        pricing, "fetch_openrouter",
        lambda: {"openai/gpt-4o": {"input": 3e-06, "output": 4e-06}},
    )
    try:
        rc, _, err = _run(["pricing", "sync", "-t", str(path)], capsys)
    finally:
        conf.chmod(0o700)
    _assert_clean_failure(rc, err, "Permission denied", str(path))


# --------------------------------------------------------------------------- #
# corrupted state file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "record",
    ['{"fleet_name": "acme", "bots": [', '["not", "an", "object"]', '{"fleet_name": "acme"}'],
    ids=["truncated", "wrong-type", "missing-fields"],
)
def test_fleet_status_corrupted_record(tmp_path, capsys, record):
    fleets = tmp_path / ".hermes" / "fleets"
    fleets.mkdir(parents=True)
    (fleets / "acme.json").write_text(record)
    rc, _, err = _run(["fleet", "status", "acme"], capsys)
    _assert_clean_failure(rc, err, "corrupted", str(fleets / "acme.json"))


def test_eval_compare_corrupted_baseline(tmp_path, capsys):
    runs = tmp_path / "data" / "nuvel" / "evalv2" / "demo"
    runs.mkdir(parents=True)
    (runs / "baseline.json").write_text("{\x00garbage")
    (runs / "20260101T000000_000000Z.json").write_text("{}")
    rc, _, err = _run(["evalv2", "compare", "demo"], capsys)
    _assert_clean_failure(rc, err, "failed to load result", "baseline.json")


# --------------------------------------------------------------------------- #
# invalid skill reference
# --------------------------------------------------------------------------- #
def test_bots_create_invalid_skill_creates_nothing(tmp_path, capsys):
    hub = tmp_path / "hub"
    (hub / "hr" / "payroll").mkdir(parents=True)
    (hub / "hr" / "payroll" / "SKILL.md").write_text("---\nname: payroll\n---\n")
    log = tmp_path / "hermes.log"
    rc, _, err = _run(
        ["bots", "create", "my-bot", "--skills", "hr/payroll,hr/nope",
         "--hub", str(hub), "--hermes-bin", _fake_hermes(tmp_path, log)],
        capsys,
    )
    _assert_clean_failure(rc, err, "skill 'hr/nope' not found in hub")
    # Partial failure: the bad ref is caught before the profile is created.
    assert not log.exists() or "profile create" not in log.read_text()
    assert not (tmp_path / ".hermes" / "profiles" / "my-bot").exists()


def test_eval_run_unknown_skill(tmp_path, capsys):
    skills = _eval_skill(tmp_path)
    rc, _, err = _run(["evalv2", "run", "no-such-skill", "--skills-dir", str(skills)], capsys)
    _assert_clean_failure(rc, err, "skill 'no-such-skill' has no eval/suite.yaml")


# --------------------------------------------------------------------------- #
# a failed write leaves the previous state file intact
# --------------------------------------------------------------------------- #
def _limit_file_size():
    # Writes past 64 bytes fail with EFBIG, as on a full disk, mid-file.
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (64, 64))


@pytest.mark.skipif(not hasattr(resource, "RLIMIT_FSIZE"), reason="needs RLIMIT_FSIZE")
def test_fleet_deploy_write_failure_keeps_previous_record(tmp_path, capsys):
    hermes_bin = _fake_hermes(tmp_path)
    path = _manifest(tmp_path, "name: acme\ncompany: Acme\nbots: []\n")
    assert _run(["fleet", "deploy", path, "--hermes-bin", hermes_bin], capsys)[0] == 0
    fleets = tmp_path / ".hermes" / "fleets"
    previous = (fleets / "acme.json").read_text()
    assert len(previous) > 64

    # Redeploy in a child whose writes fail part-way through the record.
    _manifest(tmp_path, "name: acme\ncompany: Acme Renamed\nbots: []\n")
    proc = subprocess.run(
        [sys.executable, "-m", "nuvel", "fleet", "deploy", path, "--hermes-bin", hermes_bin],
        capture_output=True, text=True, timeout=120, preexec_fn=_limit_file_size,
        env={**os.environ, "HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
             "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])},
        cwd=tmp_path,
    )
    _assert_clean_failure(proc.returncode, proc.stderr,
                          "could not write the record for fleet 'acme'", "File too large")
    assert (fleets / "acme.json").read_text() == previous
    assert json.loads(previous)["company"] == "Acme"
    assert sorted(p.name for p in fleets.iterdir()) == ["acme.json"]
