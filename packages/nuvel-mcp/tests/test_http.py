"""Streamable HTTP transport: auth, body limits, tenant stamping, CLI wiring."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import httpx  # noqa: E402  (ships with the mcp SDK)
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from nuvel_mcp import cli  # noqa: E402
from nuvel_mcp.auth import BearerAuthenticator, current_principal  # noqa: E402
from nuvel_mcp.http import build_app  # noqa: E402
from nuvel_mcp.server import HTTP_DEFAULT_TOOLS, SkillsMCPServer  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "skill_run_example.json"
DAY_FILE = Path("_runs") / "chief-of-staff.radar" / "2026-10-20.jsonl"
BASE = "http://127.0.0.1:8765"
URL = f"{BASE}/mcp"
TOKENS = {"acme": "s3cret", "globex": "other-s3cret"}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "t", "version": "0"}},
}


@pytest.fixture(autouse=True)
def _no_relay(monkeypatch):
    monkeypatch.delenv("NUVEL_FEEDBACK_URL", raising=False)


@pytest.fixture
def example():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def server(tmp_path):
    return SkillsMCPServer(None, feedback_dir=tmp_path, tools=HTTP_DEFAULT_TOOLS)


@pytest.fixture
def app(server):
    return build_app(server, BearerAuthenticator(TOKENS))


def lifespan(app):
    """Start the app's MCP session manager (entered in the test's own task)."""
    return app.router.lifespan_context(app)


def raw_client(app, **kw):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE, **kw)


async def mcp_session(app, token, fn):
    """Run ``fn(session)`` in an initialized MCP client session over ``app``."""
    async with raw_client(app, headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(URL, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)


async def test_tools_list_is_exactly_the_http_defaults(app):
    async def go(session):
        return await session.list_tools()

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert {t.name for t in result.tools} == HTTP_DEFAULT_TOOLS


async def test_record_run_trace_is_stamped_with_the_authenticated_tenant(app, tmp_path, example):
    async def go(session):
        return await session.call_tool("record_run_trace", {"trace": example})

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert result.isError is False
    assert json.loads(result.content[0].text) == {"status": "recorded", "run_id": example["run_id"]}
    line = json.loads((tmp_path / DAY_FILE).read_text())
    assert line["tenant"] == "acme"
    assert "s3cret" not in (tmp_path / DAY_FILE).read_text()
    assert current_principal.get() is None


async def test_concurrent_tenants_never_see_each_other(app, tmp_path, example):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    runs = {
        "s3cret": {**example, "started_at": now},
        "other-s3cret": {**example, "started_at": now,
                         "run_id": "11111111-1111-4111-8111-111111111111"},
    }

    def recorder(trace):
        async def go(session):
            return await session.call_tool("record_run_trace", {"trace": trace})
        return go

    async def health(session):
        return await session.call_tool(
            "check_skill_health", {"skill_name": "radar", "plugin": "chief-of-staff"})

    async with lifespan(app):
        results = await asyncio.gather(*(
            mcp_session(app, token, recorder(trace)) for token, trace in runs.items()
        ))
        body = json.loads((await mcp_session(app, "s3cret", health)).content[0].text)
    assert all(r.isError is False for r in results)
    day = now[:10]
    stored = [json.loads(x) for x in
              (tmp_path / "_runs" / "chief-of-staff.radar" / f"{day}.jsonl").read_text().splitlines()]
    assert {(r["run_id"], r["tenant"]) for r in stored} == {
        (runs["s3cret"]["run_id"], "acme"),
        (runs["other-s3cret"]["run_id"], "globex"),
    }
    # check_skill_health is principal-scoped: acme sees only its own run.
    assert body["runs"]["total"] == 1


async def test_disabled_tool_is_an_error_without_echo(app):
    async def go(session):
        return await session.call_tool("propose_improvement", {"skill_name": "ana@acme.com"})

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert result.isError is True
    assert "ana@acme.com" not in result.model_dump_json()


async def test_internal_errors_are_not_leaked_to_the_caller(tmp_path, example):
    class BrokenStore:
        def append(self, record):
            raise OSError("disk full at /srv/secret-path/_runs")

        def query(self, plugin, skill, since, tenant):
            return []

    server = SkillsMCPServer(None, feedback_dir=tmp_path, tools=HTTP_DEFAULT_TOOLS,
                             trace_store=BrokenStore())
    app = build_app(server, BearerAuthenticator(TOKENS))

    async def go(session):
        return await session.call_tool("record_run_trace", {"trace": example})

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert result.isError is True
    assert "secret-path" not in result.model_dump_json()


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": "Basic s3cret"},
])
async def test_missing_or_wrong_token_is_401(app, headers):
    async with raw_client(app) as http:
        res = await http.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})
    assert res.status_code == 401
    assert "s3cret" not in res.text


async def test_duplicate_authorization_headers_are_401(app):
    headers = list(MCP_HEADERS.items()) + [
        ("Authorization", "Bearer s3cret"), ("authorization", "Bearer s3cret"),
    ]
    async with raw_client(app) as http:
        res = await http.post("/mcp", content=json.dumps(INIT), headers=headers)
    assert res.status_code == 401


async def test_oversized_body_is_413_without_echo(app):
    body = json.dumps({**INIT, "pad": "SECRETPAYLOAD" + "x" * 70_000})
    async with raw_client(app) as http:
        res = await http.post("/mcp", content=body,
                              headers={**MCP_HEADERS, "Authorization": "Bearer s3cret"})
    assert res.status_code == 413
    assert "SECRETPAYLOAD" not in res.text


async def test_oversized_chunked_body_is_413(app):
    async def chunks():
        for _ in range(100):
            yield b"SECRETPAYLOAD" + b"x" * 9_987

    async with raw_client(app) as http:
        res = await http.post("/mcp", content=chunks(),
                              headers={**MCP_HEADERS, "Authorization": "Bearer s3cret"})
    assert res.status_code == 413
    assert "SECRETPAYLOAD" not in res.text


async def test_lying_content_length_is_413():
    """A body larger than its Content-Length claims is still capped."""
    seen = []

    async def inner(scope, receive, send):  # pragma: no cover - must not be reached
        seen.append(scope)

    from nuvel_mcp.http import BodyLimitAuthMiddleware

    mw = BodyLimitAuthMiddleware(inner, authenticator=None, max_body_bytes=100)
    messages = [{"type": "http.request", "body": b"{" + b"x" * 60, "more_body": True},
                {"type": "http.request", "body": b"x" * 60, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"host", b"127.0.0.1:8765"), (b"content-length", b"10")]}
    await mw(scope, receive, send)
    assert sent[0]["status"] == 413 and not seen


@pytest.mark.parametrize("declared", ["\u00b2".encode("latin-1"), "\u00b9\u00b3".encode("latin-1"),
                                      b"-1", b"1e3", b""])
async def test_non_ascii_digit_content_length_is_400(declared):
    """``str.isdigit`` accepts superscripts that ``int()`` rejects; still a 400."""
    seen = []

    async def inner(scope, receive, send):  # pragma: no cover - must not be reached
        seen.append(scope)

    from nuvel_mcp.http import BodyLimitAuthMiddleware

    mw = BodyLimitAuthMiddleware(inner, authenticator=None)
    sent = []

    async def receive():  # pragma: no cover - the header check runs first
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp",
             "headers": [(b"host", b"127.0.0.1:8765"), (b"content-length", declared)]}
    await mw(scope, receive, send)
    assert sent[0]["status"] == 400 and not seen


async def test_invalid_json_is_400_without_echo(app):
    async with raw_client(app) as http:
        res = await http.post("/mcp", content=b"{not json SECRETPAYLOAD",
                              headers={**MCP_HEADERS, "Authorization": "Bearer s3cret"})
    assert res.status_code == 400
    assert "SECRETPAYLOAD" not in res.text


async def test_no_authenticator_allows_local_calls(server, tmp_path, example):
    app = build_app(server, None)
    async with lifespan(app):
        async def go(session):
            return await session.call_tool("record_run_trace", {"trace": example})
        result = await mcp_session(app, "ignored", go)
    assert result.isError is False
    assert "tenant" not in json.loads((tmp_path / DAY_FILE).read_text())


# --- Host / Origin validation (DNS rebinding) -----------------------------------

async def test_non_loopback_host_is_rejected_on_a_loopback_bind(app):
    async with raw_client(app) as http:
        res = await http.post("/mcp", content=json.dumps({**INIT, "pad": "SECRETPAYLOAD"}),
                              headers={**MCP_HEADERS, "Authorization": "Bearer s3cret",
                                       "Host": "evil.example"})
    assert res.status_code == 421
    assert "SECRETPAYLOAD" not in res.text and "evil.example" not in res.text


async def test_non_loopback_host_is_rejected_without_auth_too(server):
    app = build_app(server, None)
    async with raw_client(app) as http:
        res = await http.post("/mcp", json=INIT,
                              headers={**MCP_HEADERS, "Host": "evil.example:8765"})
    assert res.status_code == 421


@pytest.mark.parametrize("origin", ["http://evil.example", "null", "http://127.0.0.1.evil.example"])
async def test_foreign_origin_is_rejected(server, origin):
    app = build_app(server, None)
    async with raw_client(app) as http:
        res = await http.post("/mcp", json=INIT, headers={**MCP_HEADERS, "Origin": origin})
    assert res.status_code == 403
    assert "evil" not in res.text


@pytest.mark.parametrize("host,origin", [
    ("localhost:8765", "http://localhost:8765"),
    ("[::1]:8765", "http://[::1]:8765"),
    ("127.0.0.1", "https://127.0.0.1"),
])
async def test_loopback_host_and_origin_are_accepted(server, host, origin):
    app = build_app(server, None)
    async with lifespan(app):
        async with raw_client(app) as http:
            res = await http.post("/mcp", json=INIT,
                                  headers={**MCP_HEADERS, "Host": host, "Origin": origin})
    assert res.status_code == 200


async def test_public_bind_does_not_check_host(server):
    app = build_app(server, BearerAuthenticator(TOKENS), bind_host="0.0.0.0")
    async with lifespan(app):
        async with raw_client(app) as http:
            res = await http.post("/mcp", json=INIT, headers={
                **MCP_HEADERS, "Authorization": "Bearer s3cret", "Host": "mcp.example.com"})
    assert res.status_code == 200


# --- --allowed-host: loopback bind behind a reverse proxy ----------------------

async def _proxied(app, host, origin=None):
    headers = {**MCP_HEADERS, "Authorization": "Bearer s3cret", "Host": host}
    if origin:
        headers["Origin"] = origin
    async with raw_client(app) as http:
        return await http.post("/mcp", json=INIT, headers=headers)


@pytest.mark.parametrize("host,origin", [
    ("mcp.example.com", None),
    ("MCP.Example.com", "https://mcp.example.com"),
    ("mcp.example.com:443", "http://mcp.example.com"),
])
async def test_allowed_host_is_accepted_on_a_loopback_bind(server, host, origin):
    app = build_app(server, BearerAuthenticator(TOKENS), allowed_hosts=["mcp.example.com"])
    async with lifespan(app):
        res = await _proxied(app, host, origin)
    assert res.status_code == 200


async def test_proxy_host_without_the_flag_is_421(app):
    assert (await _proxied(app, "mcp.example.com")).status_code == 421


@pytest.mark.parametrize("host,origin,status", [
    ("evil.example", None, 421),
    ("mcp.example.com.evil.example", None, 421),
    ("mcp.example.com", "https://evil.example", 403),
    ("mcp.example.com", "https://mcp.example.com.evil.example", 403),
])
async def test_allowed_host_does_not_open_other_hosts(server, host, origin, status):
    app = build_app(server, BearerAuthenticator(TOKENS), allowed_hosts=["mcp.example.com"])
    assert (await _proxied(app, host, origin)).status_code == status


# --- check_skill_health over HTTP: runs only ------------------------------------

async def test_http_health_returns_only_tenant_runs(app, server, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    local = SkillsMCPServer(None, feedback_dir=tmp_path)
    queued = local.handle_tools_call({"name": "propose_improvement", "arguments": {
        "skill_name": "radar", "current_version": "1.0.0",
        "issue": "PRIVATEISSUETEXT", "suggested_fix": "PRIVATEFIXTEXT"}})
    assert json.loads(queued["content"][0]["text"])["status"] == "queued"

    async def go(session):
        return await session.call_tool(
            "check_skill_health", {"skill_name": "radar", "plugin": "chief-of-staff"})

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    dumped = result.model_dump_json()
    assert "PRIVATEISSUETEXT" not in dumped and "PRIVATEFIXTEXT" not in dumped
    body = json.loads(result.content[0].text)
    assert set(body) == {"skill", "plugin", "runs"}
    assert (body["skill"], body["plugin"]) == ("radar", "chief-of-staff")
    # stdio still reports the feedback store.
    stdio = json.loads(local.handle_tools_call({"name": "check_skill_health", "arguments": {
        "skill_name": "radar"}})["content"][0]["text"])
    assert stdio["queued_proposals"] == 1


def _radar_hub(root):
    from nuvel_mcp.skills_loader import SkillsLoader

    skill = root / "skills" / "ops" / "radar"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: radar\nversion: 1.0.0\n---\n\nScan.\n")
    (root / "skills" / "index.json").write_text(json.dumps({"version": "1.0.0", "themes": {
        "ops": [{"name": "radar", "description": "d", "path": "skills/ops/radar/SKILL.md"}]}}))
    return SkillsLoader(root / "skills")


async def test_http_get_skill_returns_nothing_from_the_feedback_store(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    store = tmp_path / "store"
    local = SkillsMCPServer(None, feedback_dir=store)
    local.handle_tools_call({"name": "propose_improvement", "arguments": {
        "skill_name": "radar", "current_version": "1.0.0",
        "issue": "PRIVATEISSUETEXT", "suggested_fix": "PRIVATEFIXTEXT"}})
    hub = _radar_hub(tmp_path / "hub")
    # stdio still embeds the feedback report in get_skill.
    stdio = SkillsMCPServer(hub, feedback_dir=store)
    assert stdio.tool_get_skill({"name": "radar"})["health"]["queued_proposals"] == 1

    remote = SkillsMCPServer(hub, feedback_dir=store, tools=frozenset({"get_skill"}))
    app = build_app(remote, BearerAuthenticator(TOKENS))

    async def go(session):
        return await session.call_tool("get_skill", {"name": "radar"})

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert result.isError is not True
    dumped = result.model_dump_json()
    assert "PRIVATEISSUETEXT" not in dumped and "PRIVATEFIXTEXT" not in dumped
    assert "health" not in json.loads(result.content[0].text)


async def test_http_tools_list_marks_plugin_required(app):
    async def go(session):
        return await session.list_tools()

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    health = next(t for t in result.tools if t.name == "check_skill_health")
    assert set(health.inputSchema["required"]) == {"skill_name", "plugin"}


@pytest.mark.parametrize("args", [
    {"skill_name": "radar"},
    {"skill_name": "radar", "plugin": "Bad Plugin SECRETVALUE"},
    {"skill_name": "../SECRETVALUE", "plugin": "chief-of-staff"},
])
async def test_http_health_requires_plugin_and_never_echoes(app, args):
    async def go(session):
        return await session.call_tool("check_skill_health", args)

    async with lifespan(app):
        result = await mcp_session(app, "s3cret", go)
    assert result.isError is True
    assert "SECRETVALUE" not in result.model_dump_json()


# --- CLI wiring ---------------------------------------------------------------

@pytest.fixture
def captured(monkeypatch):
    from nuvel_mcp import http as http_mod

    calls = []
    monkeypatch.setattr(http_mod, "serve_http", lambda *a, **k: calls.append((a, k)))
    return calls


def test_cli_http_defaults_to_http_tools(captured, tmp_path):
    assert cli.main(["serve", "--transport", "http", "--feedback-dir", str(tmp_path)]) == 0
    (server, host, port, authenticator), _ = captured[0]
    assert (host, port, authenticator) == ("127.0.0.1", 8765, None)
    assert {t["name"] for t in server.handle_tools_list({})["tools"]} == HTTP_DEFAULT_TOOLS


def test_cli_http_respects_explicit_tools(captured, tmp_path):
    assert cli.main(["serve", "--transport", "http", "--feedback-dir", str(tmp_path),
                     "--tools", "record_feedback"]) == 0
    server = captured[0][0][0]
    assert {t["name"] for t in server.handle_tools_list({})["tools"]} == {"record_feedback"}


def test_cli_non_loopback_without_auth_exits_1(captured, capsys):
    assert cli.main(["serve", "--transport", "http", "--host", "0.0.0.0"]) == 1
    assert not captured
    assert "--auth" in capsys.readouterr().err


def test_cli_non_loopback_with_bearer_auth_serves(captured, monkeypatch, tmp_path):
    monkeypatch.setenv("NUVEL_MCP_TOKENS", "acme:s3cret")
    assert cli.main(["serve", "--transport", "http", "--host", "0.0.0.0", "--port", "9000",
                     "--auth", "bearer", "--feedback-dir", str(tmp_path)]) == 0
    (_, host, port, authenticator), _ = captured[0]
    assert (host, port) == ("0.0.0.0", 9000)
    assert authenticator.authenticate({"Authorization": "Bearer s3cret"}) == "acme"


def test_cli_passes_allowed_hosts(captured, monkeypatch, tmp_path):
    monkeypatch.setenv("NUVEL_MCP_TOKENS", "acme:s3cret")
    assert cli.main(["serve", "--transport", "http", "--feedback-dir", str(tmp_path),
                     "--auth", "bearer", "--allowed-host", "mcp.example.com",
                     "--allowed-host", "Other.Example.org"]) == 0
    assert captured[0][1]["allowed_hosts"] == ["mcp.example.com", "other.example.org"]


@pytest.mark.parametrize("value", ["", " ", "evil.example/x", "user@evil.example",
                                   "a b.example", "mcp.example.com:443", "http://x.example"])
def test_cli_bad_allowed_host_exits_1(captured, capsys, value):
    assert cli.main(["serve", "--transport", "http", "--allowed-host", value]) == 1
    assert not captured
    assert "--allowed-host" in capsys.readouterr().err


def test_cli_allowed_host_without_auth_exits_1(captured, capsys):
    assert cli.main(["serve", "--transport", "http", "--allowed-host", "mcp.example.com"]) == 1
    assert not captured
    assert "--auth" in capsys.readouterr().err


def test_build_app_refuses_allowed_hosts_without_authenticator(server):
    with pytest.raises(ValueError, match="authenticator"):
        build_app(server, None, allowed_hosts=("mcp.example.com",))


def test_serve_http_refuses_allowed_hosts_without_authenticator(server, monkeypatch):
    import sys
    import types
    from nuvel_mcp.http import serve_http
    ran = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: ran.append(1)))
    with pytest.raises(ValueError):
        serve_http(server, "127.0.0.1", 8765, None, allowed_hosts=["mcp.example.com"])
    assert not ran


def test_build_app_refuses_non_loopback_bind_without_authenticator(server):
    with pytest.raises(ValueError, match="authenticator"):
        build_app(server, None, bind_host="0.0.0.0")


def test_serve_http_refuses_non_loopback_bind_without_authenticator(server, monkeypatch):
    import sys
    import types
    from nuvel_mcp.http import serve_http
    ran = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: ran.append(1)))
    with pytest.raises(ValueError):
        serve_http(server, "0.0.0.0", 8765, None)
    assert not ran


def test_cli_non_loopback_with_factory_returning_none_exits_1(captured, capsys, tmp_path,
                                                                monkeypatch):
    (tmp_path / "none_auth_mod.py").write_text("def make():\n    return None\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert cli.main(["serve", "--transport", "http", "--host", "0.0.0.0",
                     "--auth", "none_auth_mod:make", "--feedback-dir", str(tmp_path)]) == 1
    assert not captured
    assert "--auth" in capsys.readouterr().err


def test_cli_allowed_host_needs_http(capsys):
    assert cli.main(["serve", "--allowed-host", "mcp.example.com"]) == 1
    assert "--transport http" in capsys.readouterr().err


@pytest.mark.parametrize("spec", ["bearer", "not-a-spec", "no_such_module_xyz:make"])
def test_cli_bad_auth_exits_1(captured, monkeypatch, capsys, spec):
    monkeypatch.delenv("NUVEL_MCP_TOKENS", raising=False)
    assert cli.main(["serve", "--transport", "http", "--auth", spec]) == 1
    assert not captured
    assert "Error" in capsys.readouterr().err
