"""The record_run_trace tool: validation, tenant stamping, storage and relay."""

import io
import json
import uuid
from pathlib import Path

import pytest

from nuvel_mcp import server as server_mod
from nuvel_mcp.auth import current_principal
from nuvel_mcp.cli import main
from nuvel_mcp.server import FEEDBACK_TOOLS, SkillsMCPServer

FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"
DAY_FILE = Path("_runs") / "chief-of-staff.radar" / "2026-10-20.jsonl"


@pytest.fixture
def example():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


def call(s, trace):
    res = s.handle_tools_call({"name": "record_run_trace", "arguments": {"trace": trace}})
    return res, json.loads(res["content"][0]["text"])


class _FakeUrlopen:
    def __init__(self, exc=None):
        self.exc, self.requests = exc, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return io.BytesIO(b"{}")


def test_listed_as_feedback_tool(tmp_path):
    assert "record_run_trace" in FEEDBACK_TOOLS
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    tools = {t["name"]: t for t in s.handle_tools_list({})["tools"]}
    desc = tools["record_run_trace"]["description"]
    assert "step catalog" in desc
    assert "injection_suspected" in desc and "forbidden_tool_requested" in desc
    assert "user content" in desc
    assert tools["record_run_trace"]["inputSchema"]["required"] == ["trace"]


def test_records_and_returns_run_id(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call(s, example)
    assert res["isError"] is False
    assert body == {"status": "recorded", "run_id": example["run_id"]}
    line = json.loads((tmp_path / DAY_FILE).read_text())
    assert line["started_at"] == "2026-10-20T17:43:00Z"
    assert "tenant" not in line


def test_duplicate_returns_duplicate(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    call(s, example)
    res, body = call(s, example)
    assert res["isError"] is False
    assert body == {"status": "duplicate", "run_id": example["run_id"]}
    assert len((tmp_path / DAY_FILE).read_text().splitlines()) == 1


def test_record_run_trace_rejects_without_echo(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res = s.handle_tools_call({"name": "record_run_trace", "arguments": {"trace": {
        **example, "plugin": {"name": "ana@acme.com", "version": "0.2.0"}}}})
    assert res["isError"] is True and "ana@acme.com" not in json.dumps(res)
    assert json.loads(res["content"][0]["text"]) == {"status": "rejected", "errors": ["/plugin/name"]}
    assert not (tmp_path / "_runs").exists()


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:00-05:00"])
def test_out_of_range_started_at_is_a_clean_rejection(tmp_path, example, ts):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call(s, {**example, "started_at": ts})
    assert res["isError"] is True
    assert body == {"status": "rejected", "errors": ["/started_at"]}


def test_missing_trace_rejected(tmp_path):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res = s.handle_tools_call({"name": "record_run_trace", "arguments": {}})
    assert res["isError"] is True
    assert json.loads(res["content"][0]["text"])["status"] == "rejected"


def test_step_id_must_carry_skill_prefix(tmp_path, example):
    example["steps"][1]["id"] = "digest.2.email"
    example["steps"][3]["id"] = "radarx.3.write"
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call(s, example)
    assert res["isError"] is True
    assert body == {"status": "rejected", "errors": ["/steps/1/id", "/steps/3/id"]}
    assert "digest.2.email" not in json.dumps(res)
    assert not (tmp_path / "_runs").exists()


def test_client_tenant_rejected(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call(s, {**example, "tenant": "evil"})
    assert res["isError"] is True and "evil" not in json.dumps(res)


def test_tenant_stamped_from_principal(tmp_path, example):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    token = current_principal.set("acme")
    try:
        call(s, example)
    finally:
        current_principal.reset(token)
    assert json.loads((tmp_path / DAY_FILE).read_text())["tenant"] == "acme"


def test_custom_trace_store_used(tmp_path, example):
    class Memory:
        def __init__(self):
            self.items = []

        def append(self, trace):
            self.items.append(trace)
            return True

        def query(self, plugin, skill, since, tenant):
            return list(self.items)

    store = Memory()
    s = SkillsMCPServer(None, feedback_dir=tmp_path, trace_store=store)
    _, body = call(s, example)
    assert body["status"] == "recorded"
    assert store.items[0]["run_id"] == example["run_id"]
    assert not (tmp_path / "_runs").exists()


def test_relay_posts_after_local_append(tmp_path, example, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    seen_local = []

    class Spy(_FakeUrlopen):
        def __call__(self, request, timeout=None):
            seen_local.append((tmp_path / DAY_FILE).is_file())
            return super().__call__(request, timeout)

    fake = Spy()
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    _, body = call(s, {**example, "started_at": "2026-10-20T17:43:59Z"})
    assert body["status"] == "recorded"
    assert seen_local == [True]
    (request,) = fake.requests
    assert request.full_url == "https://relay.example/feedback"
    sent = json.loads(request.data)
    local = json.loads((tmp_path / DAY_FILE).read_text())
    assert sent == {"type": "skill_run", "record": local}
    assert sent["record"]["started_at"] == "2026-10-20T17:43:00Z"


def test_relay_failure_still_recorded(tmp_path, example, monkeypatch):
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    fake = _FakeUrlopen(exc=server_mod.urllib.error.URLError("unreachable"))
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    res, body = call(s, example)
    assert res["isError"] is False and body["status"] == "recorded"
    assert len(fake.requests) == 1
    assert (tmp_path / DAY_FILE).is_file()


def test_duplicate_not_relayed(tmp_path, example, monkeypatch):
    s = SkillsMCPServer(None, feedback_dir=tmp_path)
    call(s, example)
    monkeypatch.setenv("NUVEL_FEEDBACK_URL", "https://relay.example/feedback")
    fake = _FakeUrlopen()
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake)
    _, body = call(s, example)
    assert body["status"] == "duplicate" and fake.requests == []


def test_cli_trace_store_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["serve", "--help"])
    assert e.value.code == 0 and "--trace-store" in capsys.readouterr().out


def test_cli_bad_trace_store_exits_1(tmp_path, capsys):
    assert main(["serve", "--feedback-dir", str(tmp_path), "--trace-store", "no_such_mod_xyz:f"]) == 1
    assert "trace store" in capsys.readouterr().err.lower()
