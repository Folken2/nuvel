"""This branch's protections on the stdlib HTTP transport (nuvel_mcp.http_transport).

Every HTTP server runs in remote mode (nothing from the feedback store is
served), exposes the gated HTTP default tool set unless ``--tools`` says
otherwise, stamps run traces with the OAuth ``client_id`` (never the subject),
and refuses cross-origin browser requests to a loopback bind.

Real servers on 127.0.0.1:0, driven with http.client — no HTTP mocks.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.server import HTTP_DEFAULT_TOOLS, SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    Client,
    call,
    dev_login,
    exchange,
    isolate_env,
    make_hub,
    make_provider,
    pkce,
    register,
    service_token,
    start,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"
FREE_TEXT = "zz-free-text-from-the-feedback-store-zz"
PHASE5_TOOLS = {"search_skills", "list_skills", "list_themes", "get_skill", "fence_value",
                "record_run_trace", "check_skill_health"}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


@pytest.fixture
def launch(tmp_path):
    started = []

    def factory(hub, *, no_auth=False, tools=None):
        provider = None if no_auth else make_provider(tmp_path / "oauth" / "oauth.json")
        httpd, thread = start(hub, provider, no_auth=no_auth, tools=tools)
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


def authed(httpd):
    return Client(httpd, service_token(httpd)) if httpd.oauth is not None else Client(httpd)


def rpc(client, message):
    session = client.initialize()
    status, _, data = client.post(message, session)
    assert status == 200, data
    return json.loads(data)


def tool_result(body):
    assert "result" in body, body
    return json.loads(body["result"]["content"][0]["text"])


def tool_names(client):
    body = rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    return {t["name"]: t for t in body["result"]["tools"]}


def trace(**overrides):
    data = json.loads(FIXTURE.read_text())
    data["run_id"] = str(uuid.uuid4())
    data["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data.update(overrides)
    return data


def stored_traces(feedback_dir: Path) -> list[dict]:
    records = []
    for path in feedback_dir.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("{"):
                    record = json.loads(line)
                    if record.get("schema") == "nuvel.skill_run/1":
                        records.append(record)
    return records


# --- (1) HTTP default tool set ------------------------------------------------------


def test_default_tool_set_is_the_phase5_set(launch, hub):
    assert HTTP_DEFAULT_TOOLS == PHASE5_TOOLS
    tools = tool_names(authed(launch(hub)))
    assert set(tools) == PHASE5_TOOLS
    # Remote mode: check_skill_health is listed with 'plugin' required.
    assert "plugin" in tools["check_skill_health"]["inputSchema"]["required"]


@pytest.mark.parametrize("tool, arguments", [
    ("record_feedback", {"skill_name": "rollback", "skill_version": "1.0.0",
                         "outcome": "partial", "severity": "minor", "what_didnt": "x"}),
    ("propose_improvement", {"skill_name": "rollback", "issue": "x", "suggested_fix": "y"}),
    ("install_skill", {"name": "rollback"}),
])
def test_gated_tools_are_refused_by_default(launch, hub, tmp_path, tool, arguments):
    if tool == "install_skill":
        arguments = {**arguments, "target_dir": str(tmp_path / "installed")}
    body = rpc(authed(launch(hub)), call(tool, arguments))
    assert "result" not in body
    assert body["error"] == {"code": -32602, "message": "Unknown tool"}
    assert not (tmp_path / "installed").exists()


def test_explicit_tools_reenable_a_gated_tool(launch, hub):
    client = authed(launch(hub, tools=frozenset(PHASE5_TOOLS | {"record_feedback"})))
    assert set(tool_names(client)) == PHASE5_TOOLS | {"record_feedback"}
    body = rpc(client, call("record_feedback", {
        "skill_name": "rollback", "skill_version": "1.0.0", "outcome": "partial",
        "severity": "minor", "what_didnt": "step 2"}))
    assert body["result"]["isError"] is False


def test_library_callers_get_remote_mode_and_default_tools(hub):
    mcp = SkillsMCPServer(SkillsLoader.from_base(hub))
    assert mcp.health_runs_only is False
    httpd = make_server(mcp, "127.0.0.1", 0, HttpConfig(), no_auth=True)
    try:
        assert mcp.health_runs_only is True
        assert {t["name"] for t in mcp.handle_tools_list({})["tools"]} == PHASE5_TOOLS
    finally:
        httpd.server_close()


def _serve_cli(args, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NUVEL_MCP_", "NUVEL_OAUTH_"))}
    env.update(NUVEL_FEEDBACK_DIR=str(tmp_path / "feedback"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "nuvel_mcp", "serve", "--transport", "http", "--port", "0",
         "--no-auth", *args],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    url = None
    deadline = time.monotonic() + 15
    while url is None and time.monotonic() < deadline:
        line = proc.stderr.readline()
        if not line:
            break
        if line.startswith("Listening on "):
            url = line.split()[2]
    if url is None:
        proc.kill()
        proc.wait()
        raise AssertionError("server did not start")
    host, port = url.removeprefix("http://").rsplit(":", 1)
    return proc, host, int(port)


def _post(host, port, body, session=None):
    conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if session:
        headers["Mcp-Session-Id"] = session
    conn.request("POST", "/mcp", body=json.dumps(body), headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, json.loads(data)


@pytest.mark.parametrize("extra, expected", [
    ([], PHASE5_TOOLS),
    (["--tools", "record_feedback,search_skills"], {"record_feedback", "search_skills"}),
])
def test_cli_http_tool_set(hub, tmp_path, extra, expected):
    proc, host, port = _serve_cli(["--skills-dir", str(hub), *extra], tmp_path)
    try:
        resp, _ = _post(host, port, INIT)
        session = resp.headers["Mcp-Session-Id"]
        _, body = _post(host, port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session)
        assert {t["name"] for t in body["result"]["tools"]} == expected
        conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.request("GET", "/healthz")
        assert json.loads(conn.getresponse().read())["tools"] == len(expected)
        conn.close()
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=TIMEOUT)


# --- (2) Nothing from the feedback store over HTTP -----------------------------------


@pytest.fixture
def queued_free_text(hub, tmp_path):
    """A queued proposal and a feedback entry, written the stdio way."""
    local = SkillsMCPServer(SkillsLoader.from_base(hub))
    for tool, args in (
        ("propose_improvement", {"skill_name": "rollback", "issue": FREE_TEXT,
                                 "suggested_fix": FREE_TEXT}),
        ("record_feedback", {"skill_name": "rollback", "skill_version": "1.0.0",
                             "outcome": "partial", "severity": "minor",
                             "what_didnt": FREE_TEXT}),
    ):
        body = local.dispatch(call(tool, args))
        assert body["result"]["isError"] is False, body
    assert FREE_TEXT in "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "feedback").rglob("*") if p.is_file())


def test_free_text_never_served_over_http(launch, hub, queued_free_text):
    client = authed(launch(hub))
    session = client.initialize()
    seen = []
    for message in (
        call("get_skill", {"name": "rollback"}),
        call("check_skill_health", {"skill_name": "rollback", "plugin": "chief-of-staff"}),
        call("search_skills", {"query": "rollback"}),
        call("list_skills", {}),
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
    ):
        status, _, data = client.post(message, session)
        assert status == 200
        seen.append(data.decode())
    assert FREE_TEXT not in "".join(seen)
    skill = tool_result(json.loads(seen[0]))
    assert "health" not in skill
    health = tool_result(json.loads(seen[1]))
    assert set(health) == {"skill", "plugin", "runs"}


@pytest.mark.parametrize("arguments", [
    {"skill_name": "zz-echo-me-zz"},
    {"skill_name": "zz-echo-me-zz", "plugin": "zz bad plugin zz"},
])
def test_check_skill_health_requires_plugin_without_echo(launch, hub, arguments):
    body = rpc(authed(launch(hub)), call("check_skill_health", arguments))
    assert body["error"]["code"] == -32602
    assert "zz" not in json.dumps(body)


# --- (3) Tenant = OAuth client_id -----------------------------------------------------


def user_token(client, username):
    _, reg = register(client)
    verifier, challenge = pkce()
    query = dev_login(client, reg["client_id"], challenge, username=username)
    status, _, body = exchange(client, reg["client_id"], query["code"], verifier)
    assert status == 200, body
    return reg["client_id"], body["access_token"]


def test_concurrent_clients_are_stamped_with_their_own_client_id(launch, hub, tmp_path):
    httpd = launch(hub)
    anon = Client(httpd)
    users = {}
    for name in ("alice", "bob"):
        client_id, token = user_token(anon, name)
        info = httpd.oauth.validate(token, None)
        assert info.client_id == client_id and info.subject
        users[name] = (Client(httpd, token), client_id, info.subject)

    per_client = 4
    errors = []
    barrier = threading.Barrier(len(users))

    def record(client):
        try:
            session = client.initialize()
            barrier.wait(TIMEOUT)
            for _ in range(per_client):
                status, _, data = client.post(call("record_run_trace", {"trace": trace()}), session)
                assert status == 200
                assert tool_result(json.loads(data))["status"] == "recorded"
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=record, args=(c,)) for c, _, _ in users.values()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(TIMEOUT * 4)
    assert not errors, errors

    records = stored_traces(tmp_path / "feedback")
    assert len(records) == per_client * len(users)
    tenants = [r.get("tenant") for r in records]
    for _, client_id, _ in users.values():
        assert tenants.count(client_id) == per_client
    raw = json.dumps(records)
    for _, _, subject in users.values():
        assert subject not in raw

    # Each client sees only its own runs.
    for client, _, _ in users.values():
        health = tool_result(rpc(client, call("check_skill_health",
                                              {"skill_name": "radar", "plugin": "chief-of-staff"})))
        assert health["runs"]["total"] == per_client


def test_no_auth_traces_carry_no_tenant(launch, hub, tmp_path):
    client = Client(launch(hub, no_auth=True))
    body = rpc(client, call("record_run_trace", {"trace": trace()}))
    assert tool_result(body)["status"] == "recorded"
    [record] = stored_traces(tmp_path / "feedback")
    assert "tenant" not in record


# --- (4) DNS rebinding: cross-origin requests to a loopback bind ---------------------


@pytest.mark.parametrize("no_auth", [False, True])
@pytest.mark.parametrize("method, path", [("POST", "/mcp"), ("GET", "/healthz"),
                                          ("GET", "/sse")])
def test_cross_origin_request_to_loopback_is_refused(launch, hub, no_auth, method, path):
    client = authed(launch(hub, no_auth=no_auth))
    body = INIT if method == "POST" else None
    status, headers, _ = client.request(method, path, body, {"Origin": "http://evil.example"})
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


# --- /healthz counts what is actually exposed -----------------------------------------


def test_healthz_counts_exposed_tools(launch, hub):
    status, _, data = Client(launch(hub)).request("GET", "/healthz")
    assert status == 200
    assert json.loads(data)["tools"] == len(PHASE5_TOOLS)


def test_healthz_feedback_only(launch):
    status, _, data = Client(launch(None)).request("GET", "/healthz")
    assert status == 200
    body = json.loads(data)
    assert body["skills"] == 0
    # Feedback-only: the hub tools are gone, leaving record_run_trace + check_skill_health.
    assert body["tools"] == 2


def test_storage_errors_are_not_leaked_to_the_http_caller(hub, tmp_path):
    """Re-pointed from the removed SDK transport: a failing trace store must not
    put its filesystem path into the response."""
    class BrokenStore:
        def append(self, record):
            raise OSError("disk full at /srv/secret-path/_runs")

        def query(self, plugin, skill, since, tenant):
            return []

    server = SkillsMCPServer(SkillsLoader.from_base(hub), feedback_dir=tmp_path,
                             tools=HTTP_DEFAULT_TOOLS, trace_store=BrokenStore())
    httpd = make_server(server, "127.0.0.1", 0, HttpConfig(ping_interval=0.2), None, no_auth=True)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        client = Client(httpd)
        session = client.initialize()
        status, _, data = client.post(call("record_run_trace", {"trace": trace()}), session)
    finally:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)
    assert status == 200
    assert "secret-path" not in (data.decode() if isinstance(data, bytes) else data)
