"""check_skill_health(plugin=...) runs block, and safe relay-failure logging."""

import io
import json
import shutil
import urllib.error
from pathlib import Path

import pytest

from nuvel_mcp import server as server_mod
from nuvel_mcp.auth import current_principal
from nuvel_mcp.server import SkillsMCPServer

FIXTURES = Path(__file__).parent / "fixtures"
OUTCOMES = ["success", "partial", "failure", "blocked", "skipped"]


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


@pytest.fixture
def example():
    return json.loads((FIXTURES / "skill_run_example.json").read_text())


def health(s, **args):
    res = s.handle_tools_call({"name": "check_skill_health", "arguments": args})
    return res, json.loads(res["content"][0]["text"])


def record(s, trace):
    res = s.handle_tools_call({"name": "record_run_trace", "arguments": {"trace": trace}})
    assert res["isError"] is False, res


def other_run(example, run_id, **over):
    return {**example, "run_id": run_id, **over}


def test_runs_block_counts_traces(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record(s, {**example, "started_at": now})
    record(s, other_run(
        example, "11111111-1111-4111-8111-111111111111", started_at=now,
        outcome="failure", errors=[{"step": "radar.2.email", "class": "auth"}],
    ))
    _, body = health(s, skill_name="radar", plugin="chief-of-staff")
    assert body["runs"] == {
        "last_30_days": {"success": 1, "partial": 0, "failure": 1, "blocked": 0, "skipped": 0},
        "errors_by_step": {"radar.2.email": {"auth": 1}},
        "total": 2,
    }


class _CustomStore:
    """A third-party TraceStore whose records may lack error fields."""

    def __init__(self, runs):
        self.runs = runs

    def append(self, record):
        return True

    def query(self, plugin, skill, since, tenant):
        return self.runs


def test_runs_summary_skips_errors_missing_step_or_class(tmp_path):
    runs = [
        {"outcome": "failure", "errors": [
            {"step": "radar.1.fetch", "class": "timeout"},
            {"class": "auth"},             # no step
            {"step": "radar.2.email"},     # no class
            {},
            "not-a-dict",
            {"step": ["x"], "class": "auth"},  # unhashable step
        ]},
        {"outcome": "success"},             # no errors key
        {"outcome": "failure", "errors": None},
    ]
    s = SkillsMCPServer(None, feedback_dir=tmp_path, trace_store=_CustomStore(runs))
    res, body = health(s, skill_name="radar", plugin="chief-of-staff")
    assert "isError" not in res or res["isError"] is False
    assert body["runs"]["errors_by_step"] == {"radar.1.fetch": {"timeout": 1}}
    assert body["runs"]["last_30_days"]["failure"] == 2
    assert body["runs"]["total"] == 3


def test_runs_block_empty(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    _, body = health(s, skill_name="radar", plugin="chief-of-staff")
    assert body["runs"] == {
        "last_30_days": dict.fromkeys(OUTCOMES, 0),
        "errors_by_step": {},
        "total": 0,
    }


def test_old_traces_excluded(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    record(s, {**example, "started_at": "2020-01-01T00:00:00Z"})
    _, body = health(s, skill_name="radar", plugin="chief-of-staff")
    assert body["runs"]["total"] == 0


def test_without_plugin_matches_pre_change_fixture(tmp_path):
    store = tmp_path / "store"
    shutil.copytree(FIXTURES / "health_store", store)
    s = SkillsMCPServer(None, feedback_dir=store)
    res = s.handle_tools_call({"name": "check_skill_health", "arguments": {"skill_name": "radar"}})
    assert res["content"][0]["text"] == (FIXTURES / "health_radar_expected.json").read_text()


def test_plugin_adds_runs_to_feedback_report(tmp_path):
    store = tmp_path / "store"
    shutil.copytree(FIXTURES / "health_store", store)
    s = SkillsMCPServer(None, feedback_dir=store)
    _, base = health(s, skill_name="radar")
    _, with_runs = health(s, skill_name="radar", plugin="chief-of-staff")
    assert {k: v for k, v in with_runs.items() if k != "runs"} == base


def test_tenant_scoping_by_principal(tmp_path, example):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    for tenant, rid in (("acme", example["run_id"]), ("other", "22222222-2222-4222-8222-222222222222")):
        token = current_principal.set(tenant)
        try:
            record(s, {**example, "run_id": rid, "started_at": now})
        finally:
            current_principal.reset(token)
    token = current_principal.set("acme")
    try:
        _, body = health(s, skill_name="radar", plugin="chief-of-staff")
    finally:
        current_principal.reset(token)
    assert body["runs"]["total"] == 1
    # No principal (stdio): every tenant is visible.
    _, body = health(s, skill_name="radar", plugin="chief-of-staff")
    assert body["runs"]["total"] == 2


def test_invalid_plugin_rejected_without_echo(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    for bad in ("Ana@Acme.com", 7, ""):
        with pytest.raises(server_mod.McpError) as e:
            health(s, skill_name="radar", plugin=bad)
        assert "Acme" not in e.value.message


def test_schema_lists_plugin(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    tools = {t["name"]: t for t in s.handle_tools_list({})["tools"]}
    props = tools["check_skill_health"]["inputSchema"]["properties"]
    assert "plugin" in props
    assert tools["check_skill_health"]["inputSchema"]["required"] == ["skill_name"]


# --- relay failure logging never carries the URL or its credentials ----------


class _Raises:
    def __init__(self, exc):
        self.exc = exc

    def __call__(self, request, timeout=None):
        raise self.exc


SECRET = "hunter2-s3cret"
URL = f"https://user:{SECRET}@relay.example/feedback"


@pytest.mark.parametrize("exc", [
    ValueError(f"nonnumeric port in {URL}"),
    urllib.error.URLError(f"cannot reach {URL}"),
    urllib.error.HTTPError(URL, 503, f"bad {URL}", {}, io.BytesIO(b"")),
])
def test_trace_relay_failure_never_logs_password(tmp_path, example, monkeypatch, capsys, exc):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", URL)
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", _Raises(exc))
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    record(s, example)
    err = capsys.readouterr().err
    assert "relay failed" in err.lower()
    assert SECRET not in err and "relay.example" not in err
    assert type(exc).__name__ in err
    if isinstance(exc, urllib.error.HTTPError):
        assert "503" in err


def test_propose_relay_failure_never_logs_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", URL)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        server_mod.urllib.request, "urlopen",
        _Raises(urllib.error.URLError(f"cannot reach {URL}")),
    )
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res = s.handle_tools_call({"name": "propose_improvement", "arguments": {
        "skill_name": "radar", "current_version": "1.0.0",
        "issue": "something is off", "suggested_fix": "fix it"}})
    assert json.loads(res["content"][0]["text"])["status"] == "queued"
    err = capsys.readouterr().err
    assert "relay failed" in err.lower()
    assert SECRET not in err and "relay.example" not in err
    assert "URLError" in err


def test_remote_mode_schema_requires_plugin(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path, health_runs_only=True)
    tools = {t["name"]: t for t in s.handle_tools_list({})["tools"]}
    assert set(tools["check_skill_health"]["inputSchema"]["required"]) == {"skill_name", "plugin"}
    # The stdio definition is untouched by a remote-mode server.
    local = SkillsMCPServer(None, feedback_dir=tmp_path)
    tools = {t["name"]: t for t in local.handle_tools_list({})["tools"]}
    assert tools["check_skill_health"]["inputSchema"]["required"] == ["skill_name"]
