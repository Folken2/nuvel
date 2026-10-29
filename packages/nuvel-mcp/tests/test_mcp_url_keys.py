"""Per-tester URL keys (``/mcp/k/<key>``) and the ``none`` login mode.

Keys are generated at runtime by the provider; no key literal appears here.
"""

from __future__ import annotations

import json
import time

import pytest

from nuvel_mcp.cli import main as cli_main
from nuvel_mcp.oauth import OAuthConfig, OAuthProvider, token_hash
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    Client,
    basic,
    client_credentials,
    isolate_env,
    make_hub,
    make_provider,
    start,
)

TOOLS_LIST = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth" / "oauth.json"


@pytest.fixture
def launch(tmp_path, store_path):
    hub = make_hub(tmp_path)
    started = []

    def factory(provider=None):
        httpd, thread = start(hub, provider or make_provider(store_path))
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


@pytest.fixture
def httpd(launch):
    return launch()


@pytest.fixture
def none_httpd(launch, store_path):
    return launch(OAuthProvider(OAuthConfig(store_path=store_path, login="none")))


def issue(httpd, name="tester"):
    record, key = httpd.oauth.create_client(name, url_key=True)
    return record["client_id"], key


def key_session(client, key):
    """initialize then tools/list on the key path -> (init status, list status, list body)."""
    path = f"/mcp/k/{key}"
    status, headers, data = client.post(INIT, path=path)
    if status != 200:
        return status, None, data
    session = headers["Mcp-Session-Id"]
    status2, _, data2 = client.post(TOOLS_LIST, session, path=path)
    return status, status2, data2


# --- the key works ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["dev", "none"])
def test_key_path_initializes_and_lists_tools(launch, store_path, mode):
    provider = (make_provider(store_path) if mode == "dev" else
                OAuthProvider(OAuthConfig(store_path=store_path, login="none")))
    httpd = launch(provider)
    client_id, key = issue(httpd)
    init_status, list_status, body = key_session(Client(httpd), key)
    assert (init_status, list_status) == (200, 200)
    names = {t["name"] for t in json.loads(body)["result"]["tools"]}
    assert "search_skills" in names and "install_skill" not in names  # default HTTP set


def test_key_principal_is_the_client_tenant(httpd):
    client_id, key = issue(httpd)
    status, headers, _ = Client(httpd).post(INIT, path=f"/mcp/k/{key}")
    assert status == 200
    session = httpd.sessions._sessions[headers["Mcp-Session-Id"]]
    assert session.principal == (client_id, f"client:{client_id}")
    info = httpd.oauth.validate_url_key(key)
    assert info.client_id == client_id
    assert info.scope == frozenset({"skills:read", "skills:write"})


def test_key_session_is_bound_to_its_key(httpd):
    _, key_a = issue(httpd, "a")
    _, key_b = issue(httpd, "b")
    client = Client(httpd)
    _, headers, _ = client.post(INIT, path=f"/mcp/k/{key_a}")
    session = headers["Mcp-Session-Id"]
    assert client.post(TOOLS_LIST, session, path=f"/mcp/k/{key_b}")[0] == 404
    assert client.request("DELETE", f"/mcp/k/{key_a}",
                          headers={"Mcp-Session-Id": session})[0] == 204


@pytest.mark.file_store
def test_store_keeps_only_the_hash(httpd, store_path):
    client_id, key = issue(httpd)
    raw = store_path.read_text()
    assert key not in raw
    data = json.loads(raw)
    assert data["url_keys"][token_hash(key)]["client_id"] == client_id
    client = data["clients"][client_id]
    assert client["grant_types"] == [] and client["url_key"] is True
    assert "client_secret_hash" not in client


def test_url_key_client_cannot_use_token_or_authorize(httpd):
    client_id, key = issue(httpd)
    anon = Client(httpd)
    status, body = client_credentials(anon, client_id, key)
    assert status == 401 and body["error"] == "invalid_client"
    status, _, _ = anon.form("/token", {"grant_type": "client_credentials",
                                        "client_id": client_id})
    assert status == 400


# --- refusals --------------------------------------------------------------------


def test_wrong_key_is_401_without_resource_metadata(httpd):
    issue(httpd)
    status, headers, data = Client(httpd).post(INIT, path="/mcp/k/nvuk_wrong")
    assert status == 401
    assert headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert "nvuk_wrong" not in data.decode()


@pytest.mark.parametrize("path", ["/mcp/k/", "/mcp/k", "/mcp/k/a/b", "/sse/k/x",
                                  "/messages/k/x"])
def test_malformed_and_legacy_key_paths_are_refused(httpd, path):
    status, _, data = Client(httpd).post(INIT, path=path)
    assert status == 404 and "/k/" not in data.decode()


def test_legacy_sse_with_key_is_404(httpd):
    _, key = issue(httpd)
    stream, resp = Client(httpd).open_stream(f"/sse/k/{key}")
    try:
        assert resp.status == 404
    finally:
        stream.close()


def test_key_plus_authorization_header_is_401(httpd):
    _, key = issue(httpd)
    record, secret = httpd.oauth.create_client("svc")
    token = httpd.oauth.token({"grant_type": "client_credentials"},
                              basic(record["client_id"], secret), httpd.url)["access_token"]
    status, _, _ = Client(httpd, token).post(INIT, path=f"/mcp/k/{key}")
    assert status == 401


@pytest.mark.file_store
@pytest.mark.parametrize("how", ["client_id", "key", "url", "delete"])
def test_revoked_key_is_401(httpd, store_path, monkeypatch, capsys, how):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    client_id, key = issue(httpd)
    client = Client(httpd)
    assert key_session(client, key)[:2] == (200, 200)
    if how == "delete":
        assert cli_main(["client", "delete", client_id]) == 0
    else:
        value = {"client_id": client_id, "key": key,
                 "url": f"https://mcp.example.test/mcp/k/{key}"}[how]
        assert cli_main(["token", "revoke", value]) == 0
        out = capsys.readouterr().out
        assert "1 URL key(s)" in out and key not in out
    assert client.post(INIT, path=f"/mcp/k/{key}")[0] == 401
    assert httpd.oauth.validate_url_key(key) is None


def test_revoking_unknown_key_fails(httpd, store_path, monkeypatch):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    assert cli_main(["token", "revoke", "nvuk_unknown"]) == 1


def test_revoked_key_closes_open_stream(httpd):
    _, key = issue(httpd)
    client = Client(httpd)
    path = f"/mcp/k/{key}"
    _, headers, _ = client.post(INIT, path=path)
    stream, resp = client.open_stream(path, {"Mcp-Session-Id": headers["Mcp-Session-Id"]})
    try:
        assert resp.status == 200
        httpd.oauth.revoke(key)
        deadline = time.monotonic() + TIMEOUT
        while resp.readline():  # the server re-checks the key on each ping
            assert time.monotonic() < deadline, "stream outlived its revoked key"
    finally:
        stream.close()


# --- nothing prints the key ------------------------------------------------------


def test_access_log_never_contains_the_key(httpd, capsys):
    _, key = issue(httpd)
    client = Client(httpd)
    key_session(client, key)
    client.post(INIT, path=f"/mcp/k/{key}x")  # a wrong key is redacted too
    client.post(INIT, path=f"/mcp/k/{key}?q=1")
    client.post(INIT, path=f"/mcp/k/{key}/extra")
    err = capsys.readouterr().err
    assert "/mcp/k/<redacted>" in err
    assert key not in err and key[5:20] not in err


def test_redact_helper():
    from nuvel_mcp.http_transport import _redact

    assert _redact('"POST /mcp/k/abc-_1 HTTP/1.1"') == '"POST /mcp/k/<redacted> HTTP/1.1"'
    assert _redact("/sse/k/abc/def?x=1") == "/sse/k/<redacted>/def?<redacted>"
    assert _redact("oops nvuk_Abc-_9 here") == "oops <redacted> here"


# --- login mode none -------------------------------------------------------------


@pytest.mark.parametrize("method,path", [("POST", "/register"), ("GET", "/authorize"),
                                         ("GET", "/oauth/callback"),
                                         ("POST", "/oauth/dev-login")])
def test_none_mode_has_no_interactive_routes(none_httpd, method, path):
    assert Client(none_httpd).request(method, path, {})[0] == 404


def test_none_mode_metadata_and_client_credentials(none_httpd):
    anon = Client(none_httpd)
    meta = json.loads(anon.request("GET", "/.well-known/oauth-authorization-server")[2])
    assert "authorization_endpoint" not in meta and "registration_endpoint" not in meta
    assert meta["grant_types_supported"] == ["client_credentials"]
    record, secret = none_httpd.oauth.create_client("svc")
    status, body = client_credentials(anon, record["client_id"], secret)
    assert status == 200
    assert Client(none_httpd, body["access_token"]).post(INIT)[0] == 200


def test_none_mode_bearer_401_still_points_at_metadata(none_httpd):
    status, headers, _ = Client(none_httpd).post(INIT)
    assert status == 401 and "resource_metadata=" in headers["WWW-Authenticate"]


# --- CLI -------------------------------------------------------------------------


def _serve(monkeypatch, args):
    seen = {}

    def fake_serve_http(server, host, port, config, provider, no_auth=False):
        seen.update(host=host, provider=provider)

    monkeypatch.setattr("nuvel_mcp.http_transport.serve_http", fake_serve_http)
    rc = cli_main(["serve", "--transport", "http", "--port", "0", *args])
    return rc, seen


REMOTE = ["--host", "0.0.0.0", "--allow-remote"]


def test_cli_none_mode_starts_remote_without_github(monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_OAUTH_LOGIN", "none")
    monkeypatch.setenv("NUVEL_MCP_PUBLIC_URL", "https://mcp.example.test")
    rc, seen = _serve(monkeypatch, REMOTE)
    assert rc == 0 and seen["provider"].config.login_mode == "none"
    assert "NUVEL_OAUTH_ALLOWED_USERS" not in capsys.readouterr().err


def test_cli_none_mode_still_needs_public_url(monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_OAUTH_LOGIN", "none")
    rc, _ = _serve(monkeypatch, REMOTE)
    assert rc == 2 and "NUVEL_MCP_PUBLIC_URL" in capsys.readouterr().err


def test_cli_none_mode_warns_about_ignored_login_settings(monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_OAUTH_LOGIN", "none")
    monkeypatch.setenv("NUVEL_OAUTH_GITHUB_CLIENT_ID", "id-from-env")
    monkeypatch.setenv("NUVEL_OAUTH_GITHUB_CLIENT_SECRET", "secret-from-env")
    rc, seen = _serve(monkeypatch, [])
    assert rc == 0 and seen["provider"].config.login_mode == "none"
    assert "GitHub credentials are ignored" in capsys.readouterr().err


def test_cli_unknown_login_value_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_OAUTH_LOGIN", "github-ish")
    rc, _ = _serve(monkeypatch, [])
    assert rc == 2 and "NUVEL_OAUTH_LOGIN" in capsys.readouterr().err


def test_cli_client_create_url_key(store_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_MCP_PUBLIC_URL", "https://mcp.example.test/")
    assert cli_main(["client", "create", "--name", "alice", "--url-key"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    key = out["url_key"]
    assert key.startswith("nvuk_")
    assert out["connector_url"] == f"https://mcp.example.test/mcp/k/{key}"
    assert out["client_name"] == "alice" and "client_secret" not in out
    assert key not in captured.err and key not in store_path.read_text()

    assert cli_main(["client", "list"]) == 0
    assert "url-key" in capsys.readouterr().out


def test_cli_client_create_url_key_without_public_url(capsys):
    assert cli_main(["client", "create", "--name", "bob", "--url-key"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["connector_url"] == f"/mcp/k/{out['url_key']}"
    assert "NUVEL_MCP_PUBLIC_URL" in captured.err


def test_cli_url_key_and_redirect_uri_are_exclusive(capsys):
    assert cli_main(["client", "create", "--name", "x", "--url-key",
                     "--redirect-uri", "https://app.example.test/cb"]) == 2
    assert "--url-key" in capsys.readouterr().err
