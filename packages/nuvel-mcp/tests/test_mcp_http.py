"""Tests for the Skills MCP HTTP transports (nuvel_mcp.http_transport).

Each test binds a real server on 127.0.0.1:0 in a background thread and drives
it with ``http.client`` — no mocks of the HTTP layer. The server is gated by
OAuth (as in production), so the ``client`` fixture carries a real
client_credentials access token; auth itself is covered in test_mcp_oauth.py.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nuvel_mcp.cli import main as cli_main
from nuvel_mcp.http_transport import HttpConfig, prefers_sse
from nuvel_mcp.server import HTTP_DEFAULT_TOOLS, SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    TOOLS,
    Client,
    isolate_env,
    make_hub,
    make_provider,
    read_event,
    read_frame,
    service_token,
    start,
)
from _mcp_http_helpers import call as _call

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


@pytest.fixture
def server_factory(hub, tmp_path):
    started = []

    def factory(tools=None, trace_store=None, **config):
        httpd, thread = start(hub, make_provider(tmp_path / "oauth.json"), tools=tools,
                              trace_store=trace_store, **config)
        started.append((httpd, thread))
        return Client(httpd, service_token(httpd))

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


@pytest.fixture
def client(server_factory):
    return server_factory()


# --- Streamable HTTP --------------------------------------------------------------


def test_initialize_returns_session_id_and_result(client):
    status, headers, data = client.post(INIT)
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    session = headers["Mcp-Session-Id"]
    assert len(session) >= 40  # token_urlsafe(32)
    body = json.loads(data)
    assert body["id"] == 1
    assert body["result"]["serverInfo"]["name"] == "nuvel-skills"
    assert "tools" in body["result"]["capabilities"]


def test_each_initialize_gets_a_fresh_session(client):
    assert client.initialize() != client.initialize()


def test_tool_call_with_session_matches_stdio_dispatch(client, hub):
    session = client.initialize()
    call = _call("search_skills", {"query": "installer transaction"})
    status, headers, data = client.post(call, session)
    assert status == 200
    assert headers["Mcp-Session-Id"] == session
    body = json.loads(data)
    # Same dispatcher as stdio: byte-identical result shape.
    expected = SkillsMCPServer(SkillsLoader.from_base(hub)).dispatch(call)
    assert body == expected
    names = [s["name"] for s in json.loads(body["result"]["content"][0]["text"])["results"]]
    assert names[0] == "installer-transaction-design"


def test_tools_list_over_http(client):
    session = client.initialize()
    status, _, data = client.post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session)
    assert status == 200
    names = {t["name"] for t in json.loads(data)["result"]["tools"]}
    assert names == HTTP_DEFAULT_TOOLS


@pytest.mark.parametrize("session", [None, "not-a-real-session"])
def test_missing_or_unknown_session_is_404(client, session):
    status, _, data = client.post(_call("list_themes", {}), session)
    assert status == 404
    body = json.loads(data)
    assert body["jsonrpc"] == "2.0" and body["error"]["message"] == "Session not found"


def test_notification_is_202_with_empty_body(client):
    session = client.initialize()
    status, _, data = client.post(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, session
    )
    assert status == 202
    assert data == b""


def test_batch_returns_array_and_skips_notifications(client):
    session = client.initialize()
    batch = [
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
        _call("list_themes", {}, msg_id=11),
        7,
    ]
    status, _, data = client.post(batch, session)
    assert status == 200
    body = json.loads(data)
    assert [r["id"] for r in body] == [10, 11, None]
    assert body[2]["error"]["code"] == -32600


def test_initialize_must_not_be_batched(client):
    status, _, data = client.post([INIT])
    assert status == 400
    assert json.loads(data)["error"]["code"] == -32600


def test_parse_error_is_400(client):
    status, _, data = client.post("{not json")
    assert status == 400
    assert json.loads(data)["error"]["code"] == -32700


def test_post_returns_sse_when_accept_prefers_it(client):
    session = client.initialize()
    status, headers, data = client.post(
        _call("list_themes", {}), session, headers={"Accept": "text/event-stream"}
    )
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    text = data.decode()
    assert text.startswith("event: message\ndata: ")
    assert text.endswith("\n\n")
    payload = json.loads(text.split("data: ", 1)[1])
    assert payload["id"] == 2 and "result" in payload


def test_prefers_sse():
    assert prefers_sse("text/event-stream")
    assert prefers_sse("application/json;q=0.5, text/event-stream")
    assert not prefers_sse("application/json, text/event-stream")
    assert not prefers_sse("*/*")
    assert not prefers_sse(None)


def test_get_mcp_stream_frames_and_pings(client):
    session = client.initialize()
    conn, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "text/event-stream"
        assert read_frame(resp)["comments"] == ["stream open"]
        # Server -> client push lands on the stream as an SSE "message" event.
        note = {"jsonrpc": "2.0", "method": "notifications/resources/list_changed"}
        client.httpd.sessions.get(session, "streamable", client.principal).send(note)
        assert json.loads(read_event(resp)) == note
        # Idle stream gets comment pings (ping_interval=0.2 in tests).
        assert read_frame(resp)["comments"] == ["ping"]
    finally:
        conn.close()


def test_get_mcp_requires_sse_accept_and_session(client):
    session = client.initialize()
    status, headers, _ = client.request("GET", "/mcp", headers={
        "Accept": "application/json", "Mcp-Session-Id": session})
    assert status == 405
    status, _, _ = client.request("GET", "/mcp", headers={
        "Accept": "text/event-stream", "Mcp-Session-Id": "nope"})
    assert status == 404


def test_one_get_stream_per_session(client):
    session = client.initialize()
    first, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        assert resp.status == 200
        assert read_frame(resp)["comments"] == ["stream open"]
        # A second concurrent GET on the same session is refused, without echo.
        second, resp2 = client.open_stream("/mcp", {"Mcp-Session-Id": session})
        try:
            assert resp2.status == 409
            body = resp2.read()
            assert session.encode() not in body
        finally:
            second.close()
        # POSTs on the session keep working while the stream is held.
        assert client.post(_call("list_themes", {}), session)[0] == 200
        # The first stream is unaffected: pushes still land on it.
        note = {"jsonrpc": "2.0", "method": "notifications/resources/list_changed"}
        client.httpd.sessions.get(session, "streamable", client.principal).send(note)
        assert json.loads(read_event(resp)) == note
    finally:
        first.close()
    # Once the first stream is gone, a new GET is accepted.
    deadline = time.monotonic() + TIMEOUT
    status = None
    while time.monotonic() < deadline:
        stream, resp3 = client.open_stream("/mcp", {"Mcp-Session-Id": session})
        status = resp3.status
        if status == 200:
            assert read_frame(resp3)["comments"] == ["stream open"]
            stream.close()
            break
        stream.close()
        time.sleep(0.1)
    assert status == 200
    assert client.post(_call("list_themes", {}), session)[0] == 200


def test_rejected_second_stream_releases_its_connection_slot(server_factory):
    client = server_factory(max_connections=2)
    session = client.initialize()
    first, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        read_frame(resp)  # stream open — holds one of the two slots
        for _ in range(5):  # each refusal must free its slot, or the cap fills
            second, resp2 = client.open_stream("/mcp", {"Mcp-Session-Id": session})
            assert resp2.status == 409
            second.close()
        assert client.post(_call("list_themes", {}), session)[0] == 200
    finally:
        first.close()


class _UnreadableTraceStore:
    def query(self, plugin, skill, since, tenant):
        raise PermissionError("[Errno 13] Permission denied: '/secret/path/traces.jsonl'")

    def append(self, record):
        raise PermissionError("[Errno 13] Permission denied: '/secret/path/traces.jsonl'")


def test_unexpected_tool_error_is_masked_over_http(server_factory, capfd):
    client = server_factory(trace_store=_UnreadableTraceStore())
    session = client.initialize()
    status, _, data = client.post(_call(
        "check_skill_health", {"skill_name": "rollback", "plugin": "chief-of-staff"}), session)
    assert status == 200
    reply = json.loads(data)
    assert reply["error"] == {"code": -32603, "message": "Internal error"}
    assert b"/secret/path" not in data and b"PermissionError" not in data
    assert b"Permission denied" not in data
    # The detail is logged server-side for the operator.
    err = capfd.readouterr().err
    assert "PermissionError" in err and "/secret/path" in err


def test_unexpected_tool_error_detail_unchanged_on_stdio(tmp_path):
    mcp = SkillsMCPServer(None, feedback_dir=tmp_path, trace_store=_UnreadableTraceStore())
    reply = mcp.dispatch(_call(
        "check_skill_health", {"skill_name": "rollback", "plugin": "chief-of-staff"}))
    assert reply["error"]["code"] == -32603
    assert reply["error"]["message"].startswith("Internal error: ")
    assert "/secret/path" in reply["error"]["message"]


def test_delete_terminates_session_and_closes_stream(client):
    session = client.initialize()
    conn, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        read_frame(resp)  # stream open
        status, _, data = client.request("DELETE", "/mcp", headers={"Mcp-Session-Id": session})
        assert status == 204 and data == b""
        with pytest.raises(EOFError):
            while True:
                read_frame(resp)
    finally:
        conn.close()
    status, _, _ = client.post(_call("list_themes", {}), session)
    assert status == 404
    status, _, _ = client.request("DELETE", "/mcp", headers={"Mcp-Session-Id": session})
    assert status == 404


def test_idle_sessions_expire(server_factory):
    client = server_factory(session_ttl=0.2)
    session = client.initialize()
    assert client.post(_call("list_themes", {}), session)[0] == 200
    time.sleep(0.35)
    assert client.post(_call("list_themes", {}), session)[0] == 404


def test_session_cap_returns_503(server_factory):
    client = server_factory(max_sessions=2)
    client.initialize()
    client.initialize()
    status, _, data = client.post(INIT)
    assert status == 503
    assert "Mcp-Session-Id" not in data.decode()


# --- Legacy HTTP+SSE ---------------------------------------------------------------


def test_legacy_sse_endpoint_and_round_trip(client):
    conn, resp = client.open_stream("/sse")
    try:
        assert resp.status == 200
        endpoint = read_event(resp, "endpoint")
        assert endpoint.startswith("/messages?sessionId=")

        status, _, data = client.post(INIT, path=endpoint)
        assert status == 202 and data == b""
        assert json.loads(read_event(resp))["result"]["serverInfo"]["name"] == "nuvel-skills"

        status, _, _ = client.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, path=endpoint
        )
        assert status == 202

        status, _, _ = client.post(
            _call("search_skills", {"query": "installer transaction"}, msg_id=5),
            path=endpoint,
        )
        assert status == 202
        reply = json.loads(read_event(resp))
        assert reply["id"] == 5
        assert "installer-transaction-design" in reply["result"]["content"][0]["text"]
    finally:
        conn.close()

    # Closing the stream ends the legacy session.
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if client.post(_call("list_themes", {}), path=endpoint)[0] == 404:
            break
        time.sleep(0.05)
    else:
        pytest.fail("legacy session outlived its SSE stream")


def test_legacy_unknown_session_is_404(client):
    status, _, _ = client.post(_call("list_themes", {}), path="/messages?sessionId=nope")
    assert status == 404
    # A streamable session id is not valid on the legacy endpoint.
    session = client.initialize()
    status, _, _ = client.post(_call("list_themes", {}), path=f"/messages?sessionId={session}")
    assert status == 404


# --- Origin / CORS / limits / health ----------------------------------------------


def test_options_preflight_cors_headers(client):
    origin = "http://localhost:5173"
    status, headers, data = client.request("OPTIONS", "/mcp", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type, mcp-session-id",
    })
    assert status == 204 and data == b""
    assert headers["Access-Control-Allow-Origin"] == origin
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert "DELETE" in headers["Access-Control-Allow-Methods"]
    allow = headers["Access-Control-Allow-Headers"]
    for name in ("Content-Type", "Mcp-Session-Id", "Authorization"):
        assert name in allow
    assert "Mcp-Session-Id" in headers["Access-Control-Expose-Headers"]
    assert int(headers["Access-Control-Max-Age"]) > 0
    assert headers.get("Access-Control-Allow-Credentials") is None


def test_loopback_origin_is_echoed_on_responses(client):
    status, headers, _ = client.post(INIT, headers={"Origin": "http://127.0.0.1:3000"})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:3000"


@pytest.mark.parametrize("method,path", [("POST", "/mcp"), ("OPTIONS", "/mcp"),
                                         ("GET", "/sse"), ("POST", "/messages?sessionId=x")])
def test_disallowed_origin_is_403(client, method, path):
    status, headers, _ = client.request(
        method, path, INIT if method == "POST" else None,
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_allowed_origins_env(server_factory, monkeypatch):
    monkeypatch.setenv("NUVEL_MCP_ALLOWED_ORIGINS", "https://app.example.com, https://b.example")
    allowed = HttpConfig.from_env().allowed_origins
    client = server_factory(allowed_origins=allowed)
    status, headers, _ = client.post(INIT, headers={"Origin": "https://app.example.com"})
    assert status == 200
    assert headers["Access-Control-Allow-Origin"] == "https://app.example.com"
    status, _, _ = client.post(INIT, headers={"Origin": "https://app.example.com.evil.io"})
    assert status == 403


def test_oversized_body_is_413(server_factory):
    client = server_factory(max_body_bytes=1024)
    big = dict(INIT, params={"pad": "x" * 2048})
    status, _, data = client.post(big)
    assert status == 413
    assert json.loads(data)["error"]["message"] == "Request body too large"
    # The server is still healthy afterwards.
    assert client.request("GET", "/healthz")[0] == 200


def test_max_body_env_default(monkeypatch):
    assert HttpConfig.from_env().max_body_bytes == 1024 * 1024
    monkeypatch.setenv("NUVEL_MCP_MAX_BODY_BYTES", "2048")
    monkeypatch.setenv("NUVEL_MCP_MAX_SESSIONS", "3")
    monkeypatch.setenv("NUVEL_MCP_SESSION_TTL", "5")
    cfg = HttpConfig.from_env()
    assert (cfg.max_body_bytes, cfg.max_sessions, cfg.session_ttl) == (2048, 3, 5.0)


def test_healthz(client):
    status, headers, data = client.request("GET", "/healthz")
    assert status == 200
    body = json.loads(data)
    assert body["status"] == "ok" and body["transport"] == "http"
    assert body["skills"] == 3 and body["themes"] == 2
    # Counts only: unauthenticated callers learn nothing about the catalog.
    assert body["tools"] == len(HTTP_DEFAULT_TOOLS)
    assert set(body) == {"status", "transport", "skills", "themes", "tools"}
    for name in ("rollback", "onboarding", "hr", "search_skills"):
        assert name not in data.decode()


def test_healthz_reports_broken_hub(client, hub):
    (hub / "index.json").write_text("{broken", encoding="utf-8")
    status, _, data = client.request("GET", "/healthz")
    assert status == 503
    assert json.loads(data)["status"] == "error"


def test_malformed_method_gets_an_error_not_a_dropped_connection(client):
    session = client.initialize()
    status, _, data = client.post({"jsonrpc": "2.0", "id": 9, "method": 5}, session)
    assert status == 200
    assert json.loads(data)["error"]["code"] == -32600


def test_unknown_path_is_404(client):
    assert client.request("GET", "/nope")[0] == 404


# --- Gating ------------------------------------------------------------------------

PROPOSAL = {"skill_name": "rollback", "issue": "Step 3 is outdated",
            "suggested_fix": "Use the new CLI flag"}


def test_requests_without_a_token_are_401_on_both_transports(client):
    anonymous = Client(client.httpd)
    status, headers, data = anonymous.post(INIT)
    assert status == 401
    assert headers["WWW-Authenticate"].startswith(
        f'Bearer resource_metadata="http://{client.host}:{client.port}'
        '/.well-known/oauth-protected-resource"')
    assert json.loads(data)["error"]["message"].startswith("Unauthorized")
    stream, resp = anonymous.open_stream("/sse")
    try:
        assert resp.status == 401 and "WWW-Authenticate" in resp.headers
    finally:
        stream.close()


def test_every_tool_works_with_a_token_when_opted_in(server_factory, tmp_path):
    # Gated tools (free text, filesystem writes) need an explicit --tools.
    client = server_factory(tools=frozenset(TOOLS))
    session = client.initialize()
    args = {"search_skills": {"query": "rollback"}, "list_skills": {}, "list_themes": {},
            "get_skill": {"name": "rollback"},
            "install_skill": {"name": "rollback", "target_dir": str(tmp_path / "installed")},
            "fence_value": {"value": "v"},
            "propose_improvement": PROPOSAL,
            "record_feedback": {"skill_name": "rollback", "skill_version": "1.0.0",
                                "outcome": "partial", "severity": "minor",
                                "what_didnt": "step 2 was unclear"},
            "check_skill_health": {"skill_name": "rollback", "plugin": "chief-of-staff"},
            "record_run_trace": {"trace": json.loads(
                (Path(__file__).parent / "fixtures" / "skill_run_example.json").read_text())}}
    assert set(args) == set(TOOLS)
    for tool, arguments in args.items():
        status, _, data = client.post(_call(tool, arguments), session)
        assert status == 200, tool
        assert "result" in json.loads(data), (tool, data)


def test_session_is_bound_to_the_token_that_opened_it(client):
    session = client.initialize()
    other = Client(client.httpd, service_token(client.httpd))
    assert other.post(_call("list_themes", {}), session)[0] == 404
    assert client.post(_call("list_themes", {}), session)[0] == 200


# --- Logging hygiene ---------------------------------------------------------------


def test_bodies_session_ids_and_tokens_not_logged(client, capfd):
    conn, resp = client.open_stream("/sse")
    try:
        endpoint = read_event(resp, "endpoint")
        client.post(_call("search_skills", {"query": "zz-secret-query-zz"}), path=endpoint)
        read_event(resp)
    finally:
        conn.close()
    out, err = capfd.readouterr()
    assert out == ""
    assert "zz-secret-query-zz" not in err
    assert endpoint.split("=", 1)[1] not in err
    assert client.token not in err
    assert "POST /messages?<redacted>" in err


# --- CLI ----------------------------------------------------------------------------


def test_cli_refuses_non_loopback_without_allow_remote(hub, capsys):
    rc = cli_main(["serve", "--transport", "http", "--host", "0.0.0.0",
                   "--port", "0", "--skills-dir", str(hub)])
    assert rc != 0
    assert "--allow-remote" in capsys.readouterr().err


def test_cli_serves_http_and_shuts_down_on_sigterm(hub, tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NUVEL_MCP_", "NUVEL_OAUTH_"))}
    env.update(NUVEL_OAUTH_DEV_LOGIN="1", NUVEL_MCP_OAUTH_STORE=str(tmp_path / "o.json"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "nuvel_mcp", "serve", "--transport", "http",
         "--port", "0", "--skills-dir", str(hub)],
        cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        url = None
        stderr_lines = []
        deadline = time.monotonic() + 15
        while url is None and time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                break
            stderr_lines.append(line)
            if line.startswith("Listening on "):
                url = line.split()[2]
        assert url, "".join(stderr_lines)
        assert "OAuth 2.1" in "".join(stderr_lines) + proc.stderr.readline()
        host, port = url.removeprefix("http://").rsplit(":", 1)
        conn = http.client.HTTPConnection(host, int(port), timeout=TIMEOUT)
        conn.request("GET", "/healthz")
        assert json.loads(conn.getresponse().read())["skills"] == 3
        conn.close()

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=TIMEOUT) == 0
        assert proc.stdout.read() == ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# --- Slow clients, chunked bodies, duplicate credentials --------------------------


def _raw(client, head: bytes, then: bytes = b"", wait: float = 0.0) -> tuple[bytes, float]:
    """Send ``head`` (+ ``then``), return (everything read until EOF/timeout, seconds)."""
    import socket
    started = time.monotonic()
    with socket.create_connection((client.host, client.port), timeout=TIMEOUT) as sock:
        sock.sendall(head)
        if then:
            sock.sendall(then)
        chunks = []
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        except (TimeoutError, ConnectionResetError):
            pass
    return b"".join(chunks), time.monotonic() - started


def _post_head(client, length: int) -> bytes:
    return (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
            f"Authorization: Bearer {client.token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {length}\r\n\r\n").encode()


def test_lying_content_length_times_out_with_408(server_factory):
    client = server_factory(read_timeout=0.5)
    data, elapsed = _raw(client, _post_head(client, 1000), b"x" * 10)
    assert data.startswith(b"HTTP/1.1 408")
    assert b"xxxxxxxxxx" not in data
    assert elapsed < TIMEOUT


def test_stalled_headers_free_the_thread(server_factory):
    client = server_factory(read_timeout=0.5)
    data, elapsed = _raw(client, b"POST /mcp HTTP/1.1\r\nHost: x\r\n")
    assert elapsed < TIMEOUT  # connection closed by the server, not by our own timeout
    assert b"200" not in data


def test_normal_request_still_works_with_a_read_timeout(server_factory):
    client = server_factory(read_timeout=0.5)
    assert client.post(INIT)[0] == 200


def test_open_sse_stream_outlives_the_read_timeout(server_factory):
    client = server_factory(read_timeout=0.3)
    session = client.initialize()
    conn, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        assert resp.status == 200
        time.sleep(1.0)  # > 3x the read timeout, with pings flowing (ping_interval 0.2)
        status, _, _ = client.post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                   session)
        assert status in (200, 202)
        assert resp.fp is not None and not resp.isclosed()
    finally:
        conn.close()


@pytest.mark.parametrize("encoding", ["chunked", "Chunked", "CHUNKED"])
def test_chunked_body_is_411(server_factory, encoding):
    client = server_factory()
    head = (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
            f"Authorization: Bearer {client.token}\r\n"
            f"Transfer-Encoding: {encoding}\r\n\r\n0\r\n\r\n").encode()
    data, _ = _raw(client, head)
    assert data.startswith(b"HTTP/1.1 411")


@pytest.mark.parametrize("value", [
    b"abc", b"12abc", b"-1", b"-0", b"+10", b"1_0", b"0x10", b"1e3", b"",
    "\u0661\u0662".encode("utf-8"),  # Arabic-Indic digits
    b"\xb2",                         # latin-1 superscript two
])
def test_malformed_content_length_is_400(server_factory, value):
    client = server_factory()
    body = json.dumps(INIT).encode()
    head = (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
            f"Authorization: Bearer {client.token}\r\nContent-Type: application/json\r\n"
            "Connection: close\r\n").encode() + b"Content-Length: " + value + b"\r\n\r\n"
    data, _ = _raw(client, head, body)
    assert data.startswith(b"HTTP/1.1 400"), data
    assert b"Invalid Content-Length" in data
    if value:
        assert value not in data.split(b"\r\n\r\n", 1)[1]  # never echoed


@pytest.mark.parametrize("lengths", [("10", "10"), ("10", "20"), ("0", "0")])
def test_duplicate_content_length_is_400_and_closes(server_factory, lengths):
    """RFC 9112 6.3: more than one Content-Length is a request-desync risk."""
    client = server_factory()
    body = json.dumps(INIT).encode()
    head = (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
            f"Authorization: Bearer {client.token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {lengths[0]}\r\ncontent-length: {lengths[1]}\r\n\r\n").encode()
    # No "Connection: close": the server itself must close, or the leftover
    # bytes would be read as a second request on this connection.
    data, elapsed = _raw(client, head, body)
    assert data.startswith(b"HTTP/1.1 400"), data
    assert data.count(b"HTTP/1.1 ") == 1
    assert b"Invalid Content-Length" in data and b"Connection: close" in data
    assert elapsed < TIMEOUT


def test_content_length_with_chunked_transfer_encoding_is_still_refused(server_factory):
    client = server_factory()
    head = (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
            f"Authorization: Bearer {client.token}\r\nContent-Length: 5\r\n"
            "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n").encode()
    data, _ = _raw(client, head)
    assert data.startswith(b"HTTP/1.1 411"), data


def test_body_shorter_than_content_length_at_eof_is_400(server_factory):
    import socket
    client = server_factory()
    body = json.dumps(INIT).encode()  # a complete, valid JSON-RPC message ...
    head = _post_head(client, len(body) + 50)  # ... that is shorter than declared
    with socket.create_connection((client.host, client.port), timeout=TIMEOUT) as sock:
        sock.sendall(head + body)
        sock.shutdown(socket.SHUT_WR)  # EOF before the declared length
        chunks = []
        while data := sock.recv(4096):
            chunks.append(data)
    data = b"".join(chunks)
    assert data.startswith(b"HTTP/1.1 400"), data
    assert b"Mcp-Session-Id" not in data and b'"result"' not in data


def test_more_than_one_authorization_header_is_401(server_factory):
    client = server_factory()
    body = json.dumps(INIT).encode()
    for second in (client.token, "wrong"):  # a valid token must not rescue it
        head = (f"POST /mcp HTTP/1.1\r\nHost: {client.host}:{client.port}\r\n"
                f"Authorization: Bearer {client.token}\r\n"
                f"authorization: Bearer {second}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
        data, _ = _raw(client, head, body)
        assert data.startswith(b"HTTP/1.1 401"), data


# --- Total request deadline, connection cap, bad settings -------------------------


def _drip(client, head: bytes, payload: bytes, interval: float) -> tuple[bytes, float]:
    """Send ``head`` then ``payload`` one byte per ``interval`` seconds until the
    server closes the connection; return (response bytes, elapsed seconds)."""
    import socket
    started = time.monotonic()
    chunks = []
    with socket.create_connection((client.host, client.port), timeout=TIMEOUT) as sock:
        sock.sendall(head)
        sock.settimeout(interval)
        for byte in payload:
            try:
                sock.sendall(bytes([byte]))
            except OSError:
                break
            try:
                data = sock.recv(4096)
            except TimeoutError:
                continue
            except ConnectionResetError:
                break
            if not data:
                break
            chunks.append(data)
            break
        else:
            return b"", time.monotonic() - started
    return b"".join(chunks), time.monotonic() - started


def test_slow_drip_body_hits_the_total_deadline_with_408(server_factory):
    # Each gap is far under the per-recv timeout, so only the deadline can stop it.
    client = server_factory(read_timeout=5.0, request_deadline=0.6)
    data, elapsed = _drip(client, _post_head(client, 1000), b"x" * 200, 0.1)
    assert data.startswith(b"HTTP/1.1 408"), data
    assert 0.5 <= elapsed < 2.5


def test_slow_drip_headers_are_closed_at_the_total_deadline(server_factory):
    client = server_factory(read_timeout=5.0, request_deadline=0.6)
    head = b"POST /mcp HTTP/1.1\r\nHost: x\r\n"
    data, elapsed = _drip(client, head, b"X-Pad: " + b"a" * 200, 0.1)
    assert data == b"" or data.startswith(b"HTTP/1.1 4"), data
    assert elapsed < 2.5  # the server hung up; we did not run out of bytes first


def test_request_deadline_leaves_normal_requests_and_sse_alone(server_factory):
    client = server_factory(read_timeout=5.0, request_deadline=0.4, ping_interval=0.1)
    session = client.initialize()
    conn, resp = client.open_stream("/mcp", {"Mcp-Session-Id": session})
    try:
        assert resp.status == 200
        time.sleep(1.0)  # well past the request deadline
        assert client.post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, session)[0] == 200
        assert not resp.isclosed()
    finally:
        conn.close()


def test_connections_over_the_cap_get_503_and_a_slot_frees_up(server_factory):
    import socket
    client = server_factory(max_connections=2, read_timeout=5.0, request_deadline=5.0)
    stalled = [socket.create_connection((client.host, client.port), timeout=TIMEOUT)
               for _ in range(2)]
    try:
        for sock in stalled:
            sock.sendall(b"POST /mcp HTTP/1.1\r\n")
        time.sleep(0.3)  # let the accept loop hand both to handler threads
        data, _ = _raw(client, b"", wait=0)
        assert data.startswith(b"HTTP/1.1 503") and b"Retry-After: 1" in data
        stalled.pop().close()  # frees a slot once its handler sees EOF
        deadline = time.monotonic() + TIMEOUT
        status = None
        while time.monotonic() < deadline:
            status = client.post(INIT)[0]
            if status == 200:
                break
            time.sleep(0.1)
        assert status == 200
    finally:
        for sock in stalled:
            sock.close()


def test_connection_cap_503_drains_pending_bytes_before_closing(server_factory):
    """With request bytes still unread, closing would send a TCP reset, which
    can discard the 503 before the client reads it. The server drains them, so
    the client reads the 503 and then a clean end-of-stream."""
    import socket
    client = server_factory(max_connections=1)
    httpd = client.httpd
    assert httpd._slots.acquire(blocking=False)  # the cap is reached
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        with socket.create_connection(listener.getsockname(), timeout=TIMEOUT) as sock:
            body = json.dumps(INIT).encode() + b" " * 8000
            sock.sendall(_post_head(client, len(body)) + body)
            conn, addr = listener.accept()
            time.sleep(0.2)  # the request bytes are now queued on the server side
            httpd.process_request(conn, addr)
            data = sock.recv(4096)
            assert data.startswith(b"HTTP/1.1 503"), data
            assert b"Retry-After: 1" in data
            assert sock.recv(4096) == b""  # FIN, not ConnectionResetError
    finally:
        listener.close()
        httpd._slots.release()


def test_dispatch_resets_the_principal_after_a_handler_raises():
    """``_dispatch_all`` restores the caller's principal even when dispatch
    raises, so a keep-alive connection's next request never inherits it."""
    from types import SimpleNamespace

    from nuvel_mcp.auth import current_principal
    from nuvel_mcp.http_transport import McpRequestHandler

    seen = []

    def boom(messages):
        seen.append(current_principal.get())
        raise RuntimeError("handler failed")

    fake = SimpleNamespace(_dispatch_each=boom)
    info = SimpleNamespace(client_id="tenant-a")
    assert current_principal.get() is None
    with pytest.raises(RuntimeError):
        McpRequestHandler._dispatch_all(fake, [{}], info)
    assert seen == ["tenant-a"]
    assert current_principal.get() is None


def test_sessions_at_or_over_the_connection_cap_warn_at_startup(hub, tmp_path, capsys):
    from nuvel_mcp.http_transport import make_server

    for sessions, connections, warned in ((64, 128, False), (8, 8, True), (9, 8, True)):
        mcp = SkillsMCPServer(SkillsLoader.from_base(hub))
        cfg = HttpConfig(max_sessions=sessions, max_connections=connections)
        httpd = make_server(mcp, "127.0.0.1", 0, cfg, no_auth=True)
        httpd.server_close()
        err = capsys.readouterr().err
        assert ("NUVEL_MCP_MAX_SESSIONS" in err) is warned, (sessions, connections, err)
        if warned:
            assert "NUVEL_MCP_MAX_CONNECTIONS" in err


@pytest.mark.parametrize("name, value", [
    ("NUVEL_MCP_READ_TIMEOUT", "-1"), ("NUVEL_MCP_READ_TIMEOUT", "0"),
    ("NUVEL_MCP_READ_TIMEOUT", "nan"), ("NUVEL_MCP_REQUEST_DEADLINE", "-5"),
    ("NUVEL_MCP_REQUEST_DEADLINE", "0"), ("NUVEL_MCP_MAX_CONNECTIONS", "0"),
])
def test_bad_timeout_and_cap_settings_are_rejected_at_startup(monkeypatch, capsys, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        HttpConfig.from_env()
    assert cli_main(["serve", "--transport", "http", "--no-auth"]) == 2
    assert name in capsys.readouterr().err
