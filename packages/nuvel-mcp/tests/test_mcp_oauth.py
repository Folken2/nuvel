"""OAuth 2.1 gating of the Skills MCP server (nuvel_mcp.oauth + the HTTP transport).

Real servers on 127.0.0.1:0 in a thread, driven with http.client; the login
step uses the dev-login stub (or a local fake GitHub). Time-based cases move
the provider's clock rather than sleeping.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nuvel_mcp.cli import main as cli_main
from nuvel_mcp.http_transport import make_server
from nuvel_mcp.oauth import OAuthConfig, OAuthProvider, stdio_gate
from nuvel_mcp.server import HTTP_DEFAULT_TOOLS, SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    TOOLS,
    Client,
    basic,
    call,
    isolate_env,
    make_hub,
    make_provider,
    service_token,
    start,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REDIRECT = "http://127.0.0.1:33418/callback"
ALL_SCOPES = "skills:read skills:write"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth" / "oauth.json"


@pytest.fixture
def launch(hub, store_path):
    started = []

    def factory(provider=None, no_auth=False, **config):
        if provider is None and not no_auth:
            provider = make_provider(store_path)
        httpd, thread = start(hub, provider, no_auth=no_auth, **config)
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
def anon(httpd):
    return Client(httpd)


def origin(httpd):
    return httpd.url


def metadata_url(httpd):
    return f"{origin(httpd)}/.well-known/oauth-protected-resource"


def register(client, **overrides):
    body = {"client_name": "pytest-app", "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none", **overrides}
    status, _, data = client.request("POST", "/register", body)
    return status, json.loads(data)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(client_id, challenge, **extra):
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
              "code_challenge": challenge, "code_challenge_method": "S256",
              "state": "st-123", **extra}
    return "/authorize?" + urllib.parse.urlencode({k: v for k, v in params.items()
                                                   if v is not None})


def cookie_of(headers):
    match = re.search(r"nuvel_oauth_login=([^;]*)", headers.get("Set-Cookie", ""))
    return match.group(1) if match else None


def login_field(html_bytes):
    return re.search(r'name="login" value="([^"]+)"', html_bytes.decode()).group(1)


def redirect_query(headers):
    location = headers["Location"]
    assert location.startswith(REDIRECT), location
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))


def dev_login(client, client_id, challenge, username="alice", **extra):
    """Drive /authorize -> consent -> dev login; return the redirect query."""
    status, headers, page = client.request("GET", authorize_params(client_id, challenge, **extra))
    assert status == 200, page
    assert "DENY" in headers["X-Frame-Options"]
    cookie = {"Cookie": f"nuvel_oauth_login={cookie_of(headers)}"}
    login = login_field(page)
    status, _, page = client.form("/authorize", {"login": login, "action": "approve"}, cookie)
    assert status == 200 and b"DEV LOGIN" in page
    status, headers, _ = client.form("/oauth/dev-login", {"login": login, "username": username},
                                     cookie)
    assert status == 302
    return redirect_query(headers)


def exchange(client, client_id, code, verifier, **extra):
    fields = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
              "redirect_uri": REDIRECT, "client_id": client_id, **extra}
    status, headers, data = client.form("/token", {k: v for k, v in fields.items()
                                                   if v is not None})
    return status, headers, json.loads(data)


def user_token(client, **extra):
    """Register a public client and run the whole code + PKCE flow."""
    _, reg = register(client)
    verifier, challenge = pkce()
    query = dev_login(client, reg["client_id"], challenge)
    status, _, body = exchange(client, reg["client_id"], query["code"], verifier, **extra)
    assert status == 200, body
    return reg["client_id"], body


def tools_list(httpd, token):
    authed = Client(httpd, token)
    status, headers, data = authed.post(INIT)
    if status != 200:
        return status, headers, data
    session = headers["Mcp-Session-Id"]
    return authed.post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session)


# --- 401 on every gated surface -----------------------------------------------------

GATED_METHODS = [
    INIT,
    {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
    {"jsonrpc": "2.0", "id": 2, "method": "resources/read",
     "params": {"uri": "skill://hr/onboarding"}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
] + [call(tool, {}) for tool in TOOLS]


@pytest.mark.parametrize("message", GATED_METHODS,
                         ids=lambda m: m.get("params", {}).get("name") or m["method"])
@pytest.mark.parametrize("path", ["/mcp", "/messages?sessionId=x"])
def test_unauthenticated_method_is_401_with_challenge(anon, httpd, message, path):
    status, headers, data = anon.post(message, path=path)
    assert status == 401
    assert headers["WWW-Authenticate"] == (
        f'Bearer resource_metadata="{metadata_url(httpd)}", scope="{ALL_SCOPES}"')
    assert json.loads(data)["error"]["code"] == -32002


@pytest.mark.parametrize("method,path,headers", [
    ("GET", "/mcp", {"Accept": "text/event-stream", "Mcp-Session-Id": "x"}),
    ("DELETE", "/mcp", {"Mcp-Session-Id": "x"}),
    ("GET", "/sse", {"Accept": "text/event-stream"}),
])
def test_unauthenticated_stream_and_delete_are_401(anon, httpd, method, path, headers):
    status, resp_headers, _ = anon.request(method, path, headers=headers)
    assert status == 401
    assert f'resource_metadata="{metadata_url(httpd)}"' in resp_headers["WWW-Authenticate"]


@pytest.mark.parametrize("auth", ["Bearer nvat_not-a-real-token", "Bearer ", "Basic Zm9vOmJhcg=="])
def test_bad_credentials_are_401(anon, httpd, auth):
    status, headers, _ = anon.post(INIT, headers={"Authorization": auth})
    assert status == 401
    if auth.startswith("Bearer nvat"):
        assert headers["WWW-Authenticate"] == (
            f'Bearer error="invalid_token", resource_metadata="{metadata_url(httpd)}", '
            f'scope="{ALL_SCOPES}"')


def test_open_surfaces_need_no_token(anon):
    for path in ("/healthz", "/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-authorization-server"):
        assert anon.request("GET", path)[0] == 200, path


# --- Metadata --------------------------------------------------------------------------


def test_protected_resource_metadata(anon, httpd):
    status, headers, data = anon.request("GET", "/.well-known/oauth-protected-resource")
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert json.loads(data) == {
        "resource": origin(httpd),
        "authorization_servers": [origin(httpd)],
        "scopes_supported": ["skills:read", "skills:write"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Nuvel Skills MCP",
    }


def test_authorization_server_metadata(anon, httpd):
    body = json.loads(anon.request("GET", "/.well-known/oauth-authorization-server")[2])
    o = origin(httpd)
    assert body["issuer"] == o
    assert body["authorization_endpoint"] == f"{o}/authorize"
    assert body["token_endpoint"] == f"{o}/token"
    assert body["registration_endpoint"] == f"{o}/register"
    assert body["scopes_supported"] == ["skills:read", "skills:write"]
    assert body["response_types_supported"] == ["code"]
    assert body["grant_types_supported"] == [
        "authorization_code", "refresh_token", "client_credentials"]
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == [
        "none", "client_secret_basic", "client_secret_post"]


def test_public_url_sets_issuer_and_resource(launch, store_path):
    httpd = launch(public_url="https://skills.example.com")
    body = json.loads(Client(httpd).request("GET", "/.well-known/oauth-authorization-server")[2])
    assert body["issuer"] == "https://skills.example.com"
    status, headers, _ = Client(httpd).post(INIT)
    assert status == 401
    assert ('resource_metadata="https://skills.example.com/.well-known/oauth-protected-resource"'
            in headers["WWW-Authenticate"])


def test_healthz_leaks_no_catalog(anon):
    status, _, data = anon.request("GET", "/healthz")
    body = json.loads(data)
    assert status == 200 and set(body) == {"status", "transport", "skills", "themes", "tools"}
    assert all(isinstance(body[k], int) for k in ("skills", "themes", "tools"))
    for leak in ("rollback", "onboarding", "installer", "search_skills"):
        assert leak not in data.decode()


# --- Dynamic client registration ---------------------------------------------------


def test_register_public_client_gets_no_secret(anon):
    status, body = register(anon)
    assert status == 201
    assert body["client_id"].startswith("nvcl_")
    assert "client_secret" not in body
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == [REDIRECT]
    assert isinstance(body["client_id_issued_at"], int)


def test_register_confidential_client_gets_secret(anon):
    status, body = register(anon, token_endpoint_auth_method="client_secret_basic")
    assert status == 201 and body["client_secret"] and body["client_secret_expires_at"] == 0


@pytest.mark.parametrize("uri", ["https://evil.example/cb", "http://192.168.1.5/cb",
                                 "http://localhost.evil.example/cb", "javascript:alert(1)",
                                 "http://127.0.0.1:99999/cb", "http://localhost/cb#frag"])
def test_register_rejects_bad_redirect_uri(anon, uri):
    status, body = register(anon, redirect_uris=[uri])
    assert status == 400 and body["error"] == "invalid_redirect_uri"


def test_register_accepts_any_loopback_port_and_allowlisted_uri(launch, store_path):
    provider = make_provider(store_path,
                             redirect_allowlist=frozenset({"https://app.example.com/cb"}))
    client = Client(launch(provider))
    for uri in ("http://localhost/cb", "http://localhost:6274/oauth/callback",
                "http://127.0.0.1:1/x", "https://app.example.com/cb"):
        status, body = register(client, redirect_uris=[uri])
        assert status == 201, (uri, body)
    assert register(client, redirect_uris=["https://app.example.com/other"])[0] == 400


def test_register_never_grants_client_credentials(anon):
    status, body = register(anon, token_endpoint_auth_method="client_secret_basic",
                            grant_types=["client_credentials"])
    assert status == 400 and body["error"] == "invalid_client_metadata"


def test_register_is_rate_limited(launch, store_path):
    client = Client(launch(make_provider(store_path, register_limit=2)))
    assert register(client)[0] == 201
    assert register(client)[0] == 201
    status, headers, _ = client.request("POST", "/register", {"redirect_uris": [REDIRECT]})
    assert status == 429 and int(headers["Retry-After"]) > 0


def test_registered_clients_persist_in_store(anon, store_path):
    _, body = register(anon)
    assert body["client_id"] in json.loads(store_path.read_text())["clients"]


# --- Authorization code + PKCE -----------------------------------------------------


def test_full_code_flow_then_authenticated_calls(anon, httpd):
    client_id, tokens = user_token(anon)
    assert tokens["token_type"] == "Bearer" and tokens["expires_in"] == 3600
    assert tokens["scope"] == ALL_SCOPES and tokens["refresh_token"]
    status, _, data = tools_list(httpd, tokens["access_token"])
    assert status == 200
    assert {t["name"] for t in json.loads(data)["result"]["tools"]} == HTTP_DEFAULT_TOOLS


def test_code_redirect_carries_state_and_issuer(anon, httpd):
    _, reg = register(anon)
    query = dev_login(anon, reg["client_id"], pkce()[1])
    assert query["state"] == "st-123" and query["iss"] == origin(httpd)
    assert query["code"].startswith("nvac_")


def test_token_response_is_not_cacheable(anon):
    _, reg = register(anon)
    verifier, challenge = pkce()
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    status, headers, _ = exchange(anon, reg["client_id"], code, verifier)
    assert status == 200 and headers["Cache-Control"] == "no-store"


def test_wrong_code_verifier_is_invalid_grant(anon):
    _, reg = register(anon)
    _, challenge = pkce()
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    status, _, body = exchange(anon, reg["client_id"], code, pkce()[0])
    assert status == 400 and body["error"] == "invalid_grant"


def test_replayed_code_is_invalid_grant_and_revokes_first_tokens(anon, httpd):
    _, reg = register(anon)
    verifier, challenge = pkce()
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    status, _, first = exchange(anon, reg["client_id"], code, verifier)
    assert status == 200
    status, _, body = exchange(anon, reg["client_id"], code, verifier)
    assert status == 400 and body["error"] == "invalid_grant"
    assert tools_list(httpd, first["access_token"])[0] == 401


def test_expired_code_is_invalid_grant(anon, httpd):
    _, reg = register(anon)
    verifier, challenge = pkce()
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    real = time.time
    httpd.oauth.clock = lambda: real() + 61
    status, _, body = exchange(anon, reg["client_id"], code, verifier)
    assert status == 400 and body["error"] == "invalid_grant"


def test_code_is_bound_to_client_and_redirect_uri(anon):
    _, reg = register(anon)
    _, other = register(anon)
    verifier, challenge = pkce()
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    status, _, body = exchange(anon, other["client_id"], code, verifier)
    assert status == 400 and body["error"] == "invalid_grant"
    code = dev_login(anon, reg["client_id"], challenge)["code"]
    status, _, body = exchange(anon, reg["client_id"], code, verifier,
                               redirect_uri="http://127.0.0.1:1/other")
    assert status == 400 and body["error"] == "invalid_grant"


def test_unregistered_client_on_token_is_invalid_client(anon):
    status, _, body = exchange(anon, "nvcl_nope", "nvac_x", pkce()[0])
    assert status == 401 and body["error"] == "invalid_client"


def test_unsupported_grant_type(anon):
    status, _, data = anon.form("/token", {"grant_type": "password"})
    assert status == 400 and json.loads(data)["error"] == "unsupported_grant_type"


def test_repeated_token_parameter_is_rejected(anon):
    status, _, data = anon.request("POST", "/token", "grant_type=a&grant_type=b",
                                   {"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 400 and json.loads(data)["error"] == "invalid_request"


@pytest.mark.parametrize("override,error", [
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge_method": None}, "invalid_request"),
    ({"code_challenge": None}, "invalid_request"),
    ({"state": None}, "invalid_request"),
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"scope": "admin"}, "invalid_scope"),
    ({"resource": "https://other.example/mcp"}, "invalid_target"),
])
def test_authorize_errors_redirect_to_client(anon, override, error):
    _, reg = register(anon)
    params = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
              "code_challenge": pkce()[1], "code_challenge_method": "S256", "state": "s1",
              **override}
    path = "/authorize?" + urllib.parse.urlencode({k: v for k, v in params.items() if v})
    status, headers, _ = anon.request("GET", path)
    assert status == 302
    assert redirect_query(headers)["error"] == error


@pytest.mark.parametrize("client_id,redirect_uri", [
    ("nvcl_unknown", REDIRECT), (None, "http://127.0.0.1:1/not-registered")])
def test_authorize_never_redirects_to_unverified_uri(anon, client_id, redirect_uri):
    _, reg = register(anon)
    params = {"response_type": "code", "client_id": client_id or reg["client_id"],
              "redirect_uri": redirect_uri, "code_challenge": pkce()[1],
              "code_challenge_method": "S256", "state": "s"}
    status, headers, page = anon.request("GET", "/authorize?" + urllib.parse.urlencode(params))
    assert status == 400 and "Location" not in headers
    assert b"Authorization error" in page


def test_loopback_redirect_may_change_port(anon):
    _, reg = register(anon, redirect_uris=["http://127.0.0.1/callback"])
    params = {"response_type": "code", "client_id": reg["client_id"],
              "redirect_uri": "http://127.0.0.1:50123/callback", "code_challenge": pkce()[1],
              "code_challenge_method": "S256", "state": "s"}
    assert anon.request("GET", "/authorize?" + urllib.parse.urlencode(params))[0] == 200


def test_consent_requires_the_login_cookie(anon):
    _, reg = register(anon)
    status, headers, page = anon.request("GET", authorize_params(reg["client_id"], pkce()[1]))
    login = login_field(page)
    # Same login id, but no cookie (e.g. a cross-site form post): refused.
    status, headers, _ = anon.form("/authorize", {"login": login, "action": "approve"})
    assert status == 400 and "Location" not in headers
    status, _, _ = anon.form("/oauth/dev-login", {"login": login, "username": "mallory"})
    assert status == 400


def test_deny_redirects_access_denied(anon):
    _, reg = register(anon)
    status, headers, page = anon.request("GET", authorize_params(reg["client_id"], pkce()[1]))
    cookie = {"Cookie": f"nuvel_oauth_login={cookie_of(headers)}"}
    status, headers, _ = anon.form("/authorize", {"login": login_field(page), "action": "deny"},
                                   cookie)
    assert status == 302 and redirect_query(headers)["error"] == "access_denied"


def test_allowlist_blocks_other_users_and_is_rechecked(launch, store_path):
    provider = make_provider(store_path, allowed_users=frozenset({"alice"}))
    httpd = launch(provider)
    client = Client(httpd)
    _, reg = register(client)
    query = dev_login(client, reg["client_id"], pkce()[1], username="mallory")
    assert query["error"] == "access_denied" and "code" not in query

    _, tokens = user_token(client)  # alice
    assert tools_list(httpd, tokens["access_token"])[0] == 200
    provider.config.allowed_users = frozenset({"bob"})
    status, headers, data = tools_list(httpd, tokens["access_token"])
    assert status == 403  # authenticated, no longer authorized
    assert json.loads(data)["error"]["message"] == "Forbidden"


# --- GitHub login (against a local fake GitHub) ----------------------------------------


class _FakeGitHub(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        form = dict(urllib.parse.parse_qsl(
            self.rfile.read(int(self.headers["Content-Length"])).decode()))
        self.server.seen.append(form)
        ok = form.get("code") == "gh-code" and form.get("client_secret") == "gh-secret"
        self._json({"access_token": "gho_fake"} if ok else {"error": "bad_verification_code"})

    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") == "Bearer gho_fake":
            self._json({"login": "octocat"})
        else:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()


@pytest.fixture
def fake_github():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGitHub)
    server.seen = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", server
    server.shutdown()
    server.server_close()


def test_github_login_flow(launch, store_path, fake_github):
    base, gh = fake_github
    provider = make_provider(
        store_path, dev_login=False, github_client_id="gh-id", github_client_secret="gh-secret",
        allowed_users=frozenset({"octocat"}),
        github_authorize_url=f"{base}/login/oauth/authorize",
        github_token_url=f"{base}/login/oauth/access_token", github_user_url=f"{base}/user")
    httpd = launch(provider)
    client = Client(httpd)
    _, reg = register(client)
    verifier, challenge = pkce()
    status, headers, page = client.request("GET", authorize_params(reg["client_id"], challenge))
    assert b"GitHub" in page
    cookie = {"Cookie": f"nuvel_oauth_login={cookie_of(headers)}"}
    login = login_field(page)
    assert client.form("/oauth/dev-login", {"login": login, "username": "x"}, cookie)[0] == 404

    status, headers, _ = client.form("/authorize", {"login": login, "action": "approve"}, cookie)
    assert status == 302
    gh_url = urllib.parse.urlsplit(headers["Location"])
    gh_query = dict(urllib.parse.parse_qsl(gh_url.query))
    assert headers["Location"].startswith(f"{base}/login/oauth/authorize?")
    assert gh_query["client_id"] == "gh-id" and gh_query["state"] == login
    assert gh_query["redirect_uri"] == f"{origin(httpd)}/oauth/callback"

    callback = "/oauth/callback?" + urllib.parse.urlencode({"code": "gh-code", "state": login})
    status, headers, _ = client.request("GET", callback, headers=cookie)
    assert status == 302
    code = redirect_query(headers)["code"]
    assert gh.seen[0]["redirect_uri"] == f"{origin(httpd)}/oauth/callback"
    status, _, tokens = exchange(client, reg["client_id"], code, verifier)
    assert status == 200
    assert httpd.oauth.validate(tokens["access_token"], None).subject == "github:octocat"


def test_github_login_failure_redirects_error(launch, store_path, fake_github):
    base, _ = fake_github
    provider = make_provider(
        store_path, dev_login=False, github_client_id="gh-id", github_client_secret="gh-secret",
        github_token_url=f"{base}/login/oauth/access_token", github_user_url=f"{base}/user")
    client = Client(launch(provider))
    _, reg = register(client)
    status, headers, page = client.request("GET", authorize_params(reg["client_id"], pkce()[1]))
    cookie = {"Cookie": f"nuvel_oauth_login={cookie_of(headers)}"}
    login = login_field(page)
    callback = "/oauth/callback?" + urllib.parse.urlencode({"code": "wrong", "state": login})
    status, headers, _ = client.request("GET", callback, headers=cookie)
    assert status == 302 and redirect_query(headers)["error"] == "access_denied"


# --- Refresh rotation ------------------------------------------------------------------


def refresh(client, client_id, refresh_token):
    status, _, data = client.form("/token", {"grant_type": "refresh_token",
                                             "refresh_token": refresh_token,
                                             "client_id": client_id})
    return status, json.loads(data)


def test_refresh_rotates_and_old_token_stops_working(anon, httpd):
    client_id, tokens = user_token(anon)
    status, rotated = refresh(anon, client_id, tokens["refresh_token"])
    assert status == 200 and rotated["refresh_token"] != tokens["refresh_token"]
    assert tools_list(httpd, rotated["access_token"])[0] == 200

    status, body = refresh(anon, client_id, tokens["refresh_token"])
    assert status == 400 and body["error"] == "invalid_grant"
    # Reuse of a rotated-out token is treated as theft: the whole grant ends.
    assert refresh(anon, client_id, rotated["refresh_token"])[1]["error"] == "invalid_grant"
    assert tools_list(httpd, rotated["access_token"])[0] == 401


def test_refresh_token_is_bound_to_its_client(anon):
    client_id, tokens = user_token(anon)
    _, other = register(anon)
    status, body = refresh(anon, other["client_id"], tokens["refresh_token"])
    assert status == 400 and body["error"] == "invalid_grant"


# --- client_credentials ----------------------------------------------------------------


def client_credentials(client, client_id, secret, post=False, **extra):
    fields = {"grant_type": "client_credentials", **extra}
    headers = {}
    if post:
        fields.update(client_id=client_id, client_secret=secret)
    else:
        headers["Authorization"] = basic(client_id, secret)
    status, _, data = client.form("/token", fields, headers)
    return status, json.loads(data)


@pytest.mark.parametrize("post", [False, True], ids=["client_secret_basic", "client_secret_post"])
def test_client_credentials_for_operator_client(anon, httpd, post):
    record, secret = httpd.oauth.create_client("fleet")
    status, body = client_credentials(anon, record["client_id"], secret, post=post)
    assert status == 200 and "refresh_token" not in body
    assert body["scope"] == ALL_SCOPES
    assert tools_list(httpd, body["access_token"])[0] == 200


def test_client_credentials_wrong_secret_is_invalid_client(anon, httpd):
    record, _ = httpd.oauth.create_client("fleet")
    status, body = client_credentials(anon, record["client_id"], "nvcs_wrong")
    assert status == 401 and body["error"] == "invalid_client"


def test_client_credentials_refused_for_public_and_dcr_clients(anon):
    _, public = register(anon)
    status, _, data = anon.form("/token", {"grant_type": "client_credentials",
                                           "client_id": public["client_id"]})
    assert status == 400 and json.loads(data)["error"] == "unauthorized_client"
    _, confidential = register(anon, token_endpoint_auth_method="client_secret_basic")
    status, body = client_credentials(anon, confidential["client_id"],
                                      confidential["client_secret"])
    assert status == 400 and body["error"] == "unauthorized_client"


def test_read_only_scope_gets_403_on_write_tools(httpd):
    client = Client(httpd, service_token(httpd, scope="skills:read"))
    session = client.initialize()
    assert client.post(call("list_themes", {}), session)[0] == 200
    status, headers, data = client.post(call("propose_improvement", {}), session)
    assert status == 403
    assert headers["WWW-Authenticate"] == (
        f'Bearer error="insufficient_scope", resource_metadata="{metadata_url(httpd)}", '
        'scope="skills:write"')
    assert client.post(call("not_a_tool", {}), session)[0] == 403  # unknown fails closed


# --- Resource indicators -----------------------------------------------------------------


def test_token_resource_mismatch_is_rejected(anon, httpd):
    record, secret = httpd.oauth.create_client("fleet")
    status, body = client_credentials(anon, record["client_id"], secret,
                                      resource="https://other.example/mcp")
    assert status == 400 and body["error"] == "invalid_target"
    status, body = client_credentials(anon, record["client_id"], secret,
                                      resource=f"{origin(httpd)}/mcp")
    assert status == 200


def test_token_for_another_resource_is_401(anon, httpd):
    # Issued for http://localhost:<port>, presented to http://127.0.0.1:<port>.
    record, secret = httpd.oauth.create_client("fleet")
    other_origin = f"http://localhost:{httpd.server_address[1]}"
    token = httpd.oauth.token({"grant_type": "client_credentials"},
                              basic(record["client_id"], secret), other_origin)["access_token"]
    assert tools_list(httpd, token)[0] == 401


# --- Expiry / revocation ---------------------------------------------------------------


def test_expired_access_token_is_401(httpd):
    token = service_token(httpd)
    assert tools_list(httpd, token)[0] == 200
    real = time.time
    httpd.oauth.clock = lambda: real() + 3601
    status, headers, _ = tools_list(httpd, token)
    assert status == 401 and 'error="invalid_token"' in headers["WWW-Authenticate"]


def test_revoked_token_is_401_via_cli(httpd, store_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    token = service_token(httpd)
    assert tools_list(httpd, token)[0] == 200
    assert cli_main(["token", "revoke", token]) == 0
    assert "Revoked 1 access token(s)" in capsys.readouterr().out
    assert tools_list(httpd, token)[0] == 401
    assert cli_main(["token", "revoke", "nvat_unknown"]) == 1


def test_revoke_by_client_id_and_delete_client(anon, httpd, store_path, monkeypatch):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    client_id, tokens = user_token(anon)
    assert cli_main(["token", "revoke", client_id]) == 0
    assert tools_list(httpd, tokens["access_token"])[0] == 401
    assert refresh(anon, client_id, tokens["refresh_token"])[1]["error"] == "invalid_grant"

    record, secret = httpd.oauth.create_client("fleet")
    status, body = client_credentials(anon, record["client_id"], secret)
    assert cli_main(["client", "delete", record["client_id"]]) == 0
    assert tools_list(httpd, body["access_token"])[0] == 401  # client binding
    assert client_credentials(anon, record["client_id"], secret)[0] == 401


def test_revoked_token_closes_open_sse_stream(httpd):
    token = service_token(httpd)
    client = Client(httpd, token)
    stream, resp = client.open_stream("/sse")
    try:
        assert resp.status == 200
        httpd.oauth.revoke(token)
        deadline = time.monotonic() + TIMEOUT
        while resp.readline():  # the server re-checks the token on each ping
            assert time.monotonic() < deadline, "stream outlived its revoked token"
    finally:
        stream.close()


# --- Storage -----------------------------------------------------------------------------


def test_store_holds_hashes_only_with_tight_permissions(anon, httpd, store_path):
    _, confidential = register(anon, token_endpoint_auth_method="client_secret_basic")
    verifier, challenge = pkce()
    query = dev_login(anon, confidential["client_id"], challenge)
    status, _, data = anon.form("/token", {
        "grant_type": "authorization_code", "code": query["code"], "code_verifier": verifier,
        "redirect_uri": REDIRECT}, {"Authorization": basic(confidential["client_id"],
                                                           confidential["client_secret"])})
    tokens = json.loads(data)
    assert status == 200, tokens
    record, secret = httpd.oauth.create_client("fleet")
    service = client_credentials(anon, record["client_id"], secret)[1]["access_token"]

    text = store_path.read_text()
    for plaintext in (tokens["access_token"], tokens["refresh_token"], query["code"], verifier,
                      confidential["client_secret"], secret, service):
        assert plaintext not in text
    assert hashlib.sha256(tokens["access_token"].encode()).hexdigest() in text
    stored = json.loads(text)
    assert set(stored) >= {"clients", "codes", "access_tokens", "refresh_tokens"}
    rec = next(iter(stored["refresh_tokens"].values()))
    assert {"client_id", "subject", "scope", "resource", "issued_at", "expires_at",
            "revoked"} <= set(rec)
    assert stat.S_IMODE(store_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store_path.parent.stat().st_mode) == 0o700
    assert not list(store_path.parent.glob(".oauth-*.tmp"))


def test_secrets_never_logged(anon, httpd, capfd):
    client_id, tokens = user_token(anon)
    refresh(anon, client_id, tokens["refresh_token"])
    tools_list(httpd, tokens["access_token"])
    out, err = capfd.readouterr()
    for secret in (tokens["access_token"], tokens["refresh_token"]):
        assert secret not in out + err
    assert "nvac_" not in err and "code=" not in err and "/authorize?<redacted>" in err


def test_corrupt_store_fails_closed(httpd, store_path):
    token = service_token(httpd)
    store_path.write_text("{not json")
    assert tools_list(httpd, token)[0] == 401


# --- --no-auth ---------------------------------------------------------------------------


def test_no_auth_on_loopback_serves_without_tokens(launch):
    httpd = launch(no_auth=True)
    client = Client(httpd)
    session = client.initialize()
    assert client.post(call("list_themes", {}), session)[0] == 200
    assert client.request("GET", "/.well-known/oauth-authorization-server")[0] == 404


def test_no_auth_refused_on_non_loopback(hub, capsys):
    mcp = SkillsMCPServer(SkillsLoader.from_base(hub))
    with pytest.raises(ValueError):
        make_server(mcp, "0.0.0.0", 0, no_auth=True)
    rc = cli_main(["serve", "--transport", "http", "--host", "0.0.0.0",
                   "--allow-remote", "--no-auth", "--port", "0", "--skills-dir", str(hub)])
    assert rc == 2
    assert "--no-auth is refused on non-loopback" in capsys.readouterr().err


def test_gated_server_needs_a_provider(hub):
    mcp = SkillsMCPServer(SkillsLoader.from_base(hub))
    with pytest.raises(ValueError):
        make_server(mcp, "127.0.0.1", 0)


def _serve_cli(args, env_extra, timeout=15):
    """Start `nuvel mcp serve` in a subprocess; return (proc, stderr lines until Listening)."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NUVEL_MCP_", "NUVEL_OAUTH_"))}
    env.update(env_extra)
    proc = subprocess.Popen([sys.executable, "-m", "nuvel_mcp", "serve", *args],
                            cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    lines = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stderr.readline()
        if not line:
            break
        lines.append(line)
        if line.startswith("Listening on "):
            break
    return proc, lines


def test_cli_no_auth_prints_loud_warning(hub, tmp_path):
    proc, lines = _serve_cli(["--transport", "http", "--port", "0", "--no-auth",
                              "--skills-dir", str(hub)], {})
    try:
        warning = [line for line in lines if line.startswith("WARNING: --no-auth")]
        assert len(warning) == 1 and "OAuth is OFF" in warning[0]
    finally:
        proc.terminate()
        proc.wait(TIMEOUT)


def test_cli_refuses_gated_http_without_login_provider(hub, capsys):
    rc = cli_main(["serve", "--transport", "http", "--port", "0",
                   "--skills-dir", str(hub)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "NUVEL_OAUTH_GITHUB_CLIENT_ID" in err and "NUVEL_OAUTH_DEV_LOGIN=1" in err


def test_cli_refuses_dev_login_and_missing_public_url_off_loopback(hub, capsys, monkeypatch):
    monkeypatch.setenv("NUVEL_OAUTH_DEV_LOGIN", "1")
    args = ["serve", "--transport", "http", "--host", "0.0.0.0", "--allow-remote",
            "--port", "0", "--skills-dir", str(hub)]
    assert cli_main(args) == 2
    assert "DEV_LOGIN" in capsys.readouterr().err
    monkeypatch.delenv("NUVEL_OAUTH_DEV_LOGIN")
    monkeypatch.setenv("NUVEL_OAUTH_GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("NUVEL_OAUTH_GITHUB_CLIENT_SECRET", "secret")
    assert cli_main(args) == 2
    assert "NUVEL_MCP_PUBLIC_URL" in capsys.readouterr().err


def test_cli_warns_when_allowlist_unset(hub, tmp_path):
    proc, lines = _serve_cli(["--transport", "http", "--port", "0", "--skills-dir", str(hub)],
                             {"NUVEL_OAUTH_DEV_LOGIN": "1",
                              "NUVEL_MCP_OAUTH_STORE": str(tmp_path / "o.json")})
    try:
        text = "".join(lines)
        assert "NUVEL_OAUTH_ALLOWED_USERS is not set" in text
        assert "NUVEL_OAUTH_DEV_LOGIN=1" in text
    finally:
        proc.terminate()
        proc.wait(TIMEOUT)


# --- stdio -------------------------------------------------------------------------------


STDIO_INPUT = "\n".join(json.dumps(m) for m in (
    INIT, {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    call("propose_improvement", {"skill_name": "rollback", "issue": "x", "suggested_fix": "y"},
         msg_id=3),
)) + "\n"


def _run_stdio(hub, extra_args=(), env_extra=None):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("NUVEL_MCP_", "NUVEL_OAUTH_"))}
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, "-m", "nuvel_mcp", "serve",
                           "--skills-dir", str(hub), *extra_args],
                          cwd=REPO_ROOT, env=env, input=STDIO_INPUT, capture_output=True,
                          text=True, timeout=30)
    return proc, [json.loads(line) for line in proc.stdout.splitlines()]


def _stdio_token(store_path, scope=None):
    provider = OAuthProvider(OAuthConfig(store_path=store_path))
    record, secret = provider.create_client("stdio")
    form = {"grant_type": "client_credentials", **({"scope": scope} if scope else {})}
    return provider.token(form, basic(record["client_id"], secret),
                          "http://127.0.0.1:8080")["access_token"], provider


def test_stdio_is_ungated_by_default(hub, tmp_path):
    proc, replies = _run_stdio(hub, env_extra={"NUVEL_FEEDBACK_DIR": str(tmp_path / "fb")})
    assert proc.returncode == 0
    assert [r["id"] for r in replies] == [1, 2, 3]
    assert all("result" in r for r in replies)


def test_stdio_require_auth_refuses_without_token(hub, store_path):
    proc, replies = _run_stdio(hub, ["--require-auth"],
                               {"NUVEL_MCP_OAUTH_STORE": str(store_path)})
    assert proc.returncode == 0
    assert [r["id"] for r in replies] == [1, 2, 3]  # the notification gets no reply
    assert all(r["error"]["code"] == -32002 for r in replies)
    assert "NUVEL_MCP_ACCESS_TOKEN" in proc.stderr


def test_stdio_require_auth_accepts_valid_token(hub, store_path, tmp_path):
    token, _ = _stdio_token(store_path)
    proc, replies = _run_stdio(hub, ["--require-auth"], {
        "NUVEL_MCP_OAUTH_STORE": str(store_path), "NUVEL_MCP_ACCESS_TOKEN": token,
        "NUVEL_FEEDBACK_DIR": str(tmp_path / "fb")})
    assert all("result" in r for r in replies), replies
    assert token not in proc.stderr


def test_stdio_gate_scope_expiry_and_revocation(hub, store_path):
    token, provider = _stdio_token(store_path, scope="skills:read")
    server = SkillsMCPServer(SkillsLoader.from_base(hub))
    server.gate = stdio_gate(provider, token, None)
    out = io.StringIO()
    server.serve(io.StringIO(STDIO_INPUT), out)
    replies = [json.loads(line) for line in out.getvalue().splitlines()]
    assert "result" in replies[0] and "result" in replies[1]
    assert replies[2]["error"]["code"] == -32004  # read-only token, write tool

    provider.revoke(token)
    out = io.StringIO()
    server.serve(io.StringIO(json.dumps(INIT) + "\n"), out)
    assert json.loads(out.getvalue())["error"]["code"] == -32002


def test_stdio_rejects_no_auth_flag(hub, capsys):
    assert cli_main(["serve", "--skills-dir", str(hub), "--no-auth"]) == 2
    assert cli_main(["serve", "--skills-dir", str(hub), "--require-auth",
                     "--no-auth"]) == 2


# --- Client admin CLI --------------------------------------------------------------------


def test_cli_client_create_and_list(store_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    assert cli_main(["client", "create", "--name", "fleet-bot"]) == 0
    captured = capsys.readouterr()
    created = json.loads(captured.out)
    assert created["client_secret"].startswith("nvcs_")
    assert created["grant_types"] == ["client_credentials"]
    assert "only its hash is kept" in captured.err
    assert created["client_secret"] not in store_path.read_text()

    assert cli_main(["client", "create", "--name", "web",
                     "--redirect-uri", "https://app.example.com/cb"]) == 0
    web = json.loads(capsys.readouterr().out)
    assert "authorization_code" in web["grant_types"]
    assert cli_main(["client", "create", "--name", "bad",
                     "--redirect-uri", "http://evil.example/cb"]) == 2

    assert cli_main(["client", "list"]) == 0
    table = capsys.readouterr().out
    assert created["client_id"] in table and "fleet-bot" in table and "confidential" in table
    assert "nvcs_" not in table
    assert cli_main(["client", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {c["client_id"] for c in listed} == {created["client_id"], web["client_id"]}
    assert all("client_secret_hash" not in c for c in listed)


def test_cli_created_client_is_usable_by_running_server(httpd, store_path, monkeypatch, capsys):
    # The CLI writes the store while the server runs; the server sees it.
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    assert cli_main(["client", "create", "--name", "fleet"]) == 0
    created = json.loads(capsys.readouterr().out)
    status, body = client_credentials(Client(httpd), created["client_id"],
                                      created["client_secret"])
    assert status == 200
    assert tools_list(httpd, body["access_token"])[0] == 200


def test_cli_reports_corrupt_store(store_path, monkeypatch, capsys):
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    store_path.parent.mkdir(parents=True)
    store_path.write_text("[]")
    assert cli_main(["client", "list"]) == 1
    assert "not a JSON object" in capsys.readouterr().err
