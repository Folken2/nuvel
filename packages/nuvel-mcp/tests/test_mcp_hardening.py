"""Abuse limits and hardening on the HTTP transport and the OAuth server.

Per-principal session and stream caps, the per-IP connection cap (behind a
trusted proxy too), URL-key expiry, client authentication before the /token
write lock, security headers, per-subject tenants for interactive logins, the
pending-login caps and the GitHub numeric-id allow-list.

Real servers on 127.0.0.1:0, driven with http.client -- no HTTP mocks.
"""

from __future__ import annotations

import json
import time

import pytest

from nuvel_mcp.http_transport import HttpConfig
from _mcp_http_helpers import (
    INIT,
    TIMEOUT,
    Client,
    call,
    dev_login,
    exchange,
    isolate_env,
    pkce,
    register,
    make_hub,
    make_provider,
    read_frame,
    service_token,
    start,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    isolate_env(monkeypatch, tmp_path)
    for name in ("NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL", "NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL",
                 "NUVEL_MCP_MAX_CONNECTIONS_PER_IP", "NUVEL_MCP_TRUST_FORWARDED_FOR",
                 "NUVEL_MCP_HEADER_DEADLINE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def hub(tmp_path):
    return make_hub(tmp_path)


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "oauth" / "oauth.json"


@pytest.fixture
def launch(hub, store_path):
    started = []

    def factory(provider=None, **config):
        httpd, thread = start(hub, provider or make_provider(store_path), **config)
        started.append((httpd, thread))
        return httpd

    yield factory
    for httpd, thread in started:
        httpd.stop()
        httpd.server_close()
        thread.join(TIMEOUT)


def authed(httpd):
    return Client(httpd, service_token(httpd))


def eventually(probe, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(0.05)
    return False


# --- (1) per-principal session and stream caps ---------------------------------------


def test_per_principal_session_cap_is_429_and_other_principals_are_unaffected(launch):
    httpd = launch(max_sessions_per_principal=2)
    alice = authed(httpd)
    alice.initialize()
    alice.initialize()
    status, headers, data = alice.post(INIT)
    assert status == 429
    assert int(headers["Retry-After"]) >= 1
    assert "Mcp-Session-Id" not in headers and b"Mcp-Session-Id" not in data
    assert json.loads(data)["error"]["code"] == -32003
    bob = authed(httpd)  # another client: its own allowance
    bob.initialize()
    # Ending a session frees a slot for its principal.
    httpd.sessions.close_all()
    alice.initialize()


def test_global_session_cap_still_applies_across_principals(launch):
    httpd = launch(max_sessions=2, max_sessions_per_principal=8)
    authed(httpd).initialize()
    authed(httpd).initialize()
    assert authed(httpd).post(INIT)[0] == 503


def test_per_principal_stream_cap_covers_both_transports(launch):
    httpd = launch(max_streams_per_principal=1)
    alice = authed(httpd)
    session = alice.initialize()
    first, resp = alice.open_stream("/sse")
    try:
        assert resp.status == 200
        read_frame(resp)  # endpoint event: the stream holds alice's one slot
        second, resp2 = alice.open_stream("/sse")
        try:
            assert resp2.status == 429 and int(resp2.getheader("Retry-After")) >= 1
        finally:
            second.close()
        third, resp3 = alice.open_stream("/mcp", {"Mcp-Session-Id": session})
        try:
            assert resp3.status == 429
        finally:
            third.close()
        # Another principal still gets a stream.
        bob = authed(httpd)
        other, resp4 = bob.open_stream("/sse")
        try:
            assert resp4.status == 200
        finally:
            other.close()
        # POSTs are not streams: alice can still call tools.
        assert alice.post(call("list_themes", {}), session)[0] == 200
    finally:
        first.close()

    def reopened():
        stream, again = alice.open_stream("/mcp", {"Mcp-Session-Id": session})
        stream.close()
        return again.status == 200

    assert eventually(reopened)


def user_token_on(anon, client_id, username):
    """Dev login as *username* on an existing public client -> access token."""
    verifier, challenge = pkce()
    query = dev_login(anon, client_id, challenge, username=username)
    status, _, body = exchange(anon, client_id, query["code"], verifier)
    assert status == 200, body
    return body["access_token"]


def test_one_user_across_two_clients_shares_one_allowance(launch):
    # Registering more clients (DCR) must not buy a user more sessions/streams.
    httpd = launch(max_sessions_per_principal=1, max_streams_per_principal=1)
    anon = Client(httpd)
    first, second = register(anon)[1]["client_id"], register(anon)[1]["client_id"]
    alice_a = Client(httpd, user_token_on(anon, first, "alice"))
    alice_b = Client(httpd, user_token_on(anon, second, "alice"))
    bob_a = Client(httpd, user_token_on(anon, first, "bob"))
    alice_a.initialize()
    assert alice_b.post(INIT)[0] == 429
    bob_a.initialize()  # another user on the same client: own allowance
    httpd.sessions.close_all()
    stream, resp = alice_a.open_stream("/sse")
    try:
        assert resp.status == 200
        read_frame(resp)
        other, resp2 = alice_b.open_stream("/sse")
        try:
            assert resp2.status == 429
        finally:
            other.close()
    finally:
        stream.close()


def test_per_principal_caps_from_env(monkeypatch):
    monkeypatch.setenv("NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL", "3")
    monkeypatch.setenv("NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL", "2")
    cfg = HttpConfig.from_env()
    assert (cfg.max_sessions_per_principal, cfg.max_streams_per_principal) == (3, 2)
    assert (HttpConfig().max_sessions_per_principal, HttpConfig().max_streams_per_principal) \
        == (8, 4)


@pytest.mark.parametrize("name", ["NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL",
                                  "NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL",
                                  "NUVEL_MCP_MAX_CONNECTIONS_PER_IP"])
def test_per_caller_caps_must_be_positive(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match=name):
        HttpConfig.from_env()


# --- (2) per-IP connection cap, and the caller's IP behind a proxy -----------------------


def _recv_all(sock) -> bytes:
    chunks = []
    try:
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
    except (TimeoutError, ConnectionResetError):
        pass
    return b"".join(chunks)


def test_per_ip_cap_refuses_extra_connections_from_one_peer(launch):
    import socket
    httpd = launch(max_connections_per_ip=2, read_timeout=5.0, request_deadline=5.0)
    host, port = httpd.server_address[:2]
    stalled = [socket.create_connection((host, port), timeout=TIMEOUT) for _ in range(2)]
    try:
        for sock in stalled:
            sock.sendall(b"GET /healthz HTTP/1.1\r\n")  # never finished: holds its slot
        time.sleep(0.3)
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
            data = _recv_all(sock)
        assert data.startswith(b"HTTP/1.1 429"), data
        assert b"Retry-After: " in data
        stalled.pop().close()

        def served():
            try:
                return Client(httpd).request("GET", "/healthz")[0] == 200
            except ConnectionError:
                return False

        assert eventually(served)
    finally:
        for sock in stalled:
            sock.close()


def test_per_ip_cap_behind_a_trusted_proxy_keys_on_x_forwarded_for(launch):
    # Every TCP peer is the "proxy" (127.0.0.1): only X-Forwarded-For tells
    # callers apart, so the cap must not count the peer itself.
    httpd = launch(max_connections_per_ip=1, trust_forwarded_for=True)
    alice = authed(httpd)
    stream, resp = alice.open_stream("/sse", {"X-Forwarded-For": "203.0.113.5"})
    try:
        assert resp.status == 200
        read_frame(resp)  # the stream holds 203.0.113.5's one slot
        status, headers, _ = alice.request("GET", "/healthz",
                                           headers={"X-Forwarded-For": "203.0.113.5"})
        assert status == 429 and int(headers["Retry-After"]) >= 1
        # The proxy appends the real peer last; a forged left-hand entry is ignored.
        status, _, _ = alice.request("GET", "/healthz",
                                     headers={"X-Forwarded-For": "203.0.113.5, 198.51.100.7"})
        assert status == 200
        assert alice.request("GET", "/healthz")[0] == 200  # no header: the peer
    finally:
        stream.close()


def test_behind_a_proxy_unidentified_connections_get_a_short_header_deadline(launch):
    # Until its headers arrive a connection has no IP to count against, so it
    # may only sit on a global slot for the short header deadline.
    import socket
    httpd = launch(trust_forwarded_for=True, header_deadline=0.5, request_deadline=5.0,
                   read_timeout=5.0)
    with socket.create_connection(httpd.server_address[:2], timeout=TIMEOUT) as sock:
        started = time.monotonic()
        sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")  # never finished
        assert _recv_all(sock) == b""  # closed quietly
        assert time.monotonic() - started < 2.5
    # Once identified, the body still gets the full request deadline.
    alice = authed(httpd)
    body = json.dumps(INIT).encode()
    with socket.create_connection(httpd.server_address[:2], timeout=TIMEOUT) as sock:
        host = f"{alice.host}:{alice.port}"  # the token is bound to this origin
        sock.sendall(f"POST /mcp HTTP/1.1\r\nHost: {host}\r\n".encode()
                     + b"Content-Type: application/json\r\nAccept: application/json\r\n"
                     + f"Authorization: Bearer {alice.token}\r\n".encode()
                     + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode())
        time.sleep(1.0)  # past the header deadline, well inside the request one
        sock.sendall(body)
        assert _recv_all(sock).startswith(b"HTTP/1.1 200")


def test_header_deadline_from_env(monkeypatch):
    assert HttpConfig().header_deadline == 5.0
    monkeypatch.setenv("NUVEL_MCP_HEADER_DEADLINE", "2.5")
    assert HttpConfig.from_env().header_deadline == 2.5
    monkeypatch.setenv("NUVEL_MCP_HEADER_DEADLINE", "0")
    with pytest.raises(ValueError, match="NUVEL_MCP_HEADER_DEADLINE"):
        HttpConfig.from_env()


def test_forwarded_for_is_ignored_unless_trusted(launch):
    import socket
    httpd = launch(max_connections_per_ip=1)
    alice = authed(httpd)
    stream, resp = alice.open_stream("/sse", {"X-Forwarded-For": "203.0.113.5"})
    try:
        assert resp.status == 200
        read_frame(resp)
        # Same TCP peer, whatever the header claims: refused from the accept loop.
        with socket.create_connection(httpd.server_address[:2], timeout=TIMEOUT) as sock:
            sock.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n"
                         b"X-Forwarded-For: 198.51.100.7\r\n\r\n")
            assert _recv_all(sock).startswith(b"HTTP/1.1 429")
    finally:
        stream.close()


def test_register_rate_limit_keys_on_the_forwarded_client_ip(launch, store_path):
    httpd = launch(make_provider(store_path, register_limit=1), trust_forwarded_for=True)
    anon = Client(httpd)
    body = {"client_name": "x", "redirect_uris": ["http://127.0.0.1:1/cb"],
            "token_endpoint_auth_method": "none"}

    def reg(ip):
        return anon.request("POST", "/register", body, {"X-Forwarded-For": ip})[0]

    assert reg("203.0.113.5") == 201
    assert reg("203.0.113.5") == 429
    assert reg("198.51.100.7") == 201


def test_register_rate_limit_ignores_forwarded_for_unless_trusted(launch, store_path):
    httpd = launch(make_provider(store_path, register_limit=1))
    anon = Client(httpd)
    body = {"client_name": "x", "redirect_uris": ["http://127.0.0.1:1/cb"],
            "token_endpoint_auth_method": "none"}
    assert anon.request("POST", "/register", body, {"X-Forwarded-For": "203.0.113.5"})[0] == 201
    assert anon.request("POST", "/register", body, {"X-Forwarded-For": "198.51.100.7"})[0] == 429


def test_per_ip_settings_from_env(monkeypatch):
    assert HttpConfig().max_connections_per_ip == 16
    assert HttpConfig.from_env().trust_forwarded_for is False
    monkeypatch.setenv("NUVEL_MCP_MAX_CONNECTIONS_PER_IP", "5")
    monkeypatch.setenv("NUVEL_MCP_TRUST_FORWARDED_FOR", "1")
    cfg = HttpConfig.from_env()
    assert cfg.max_connections_per_ip == 5 and cfg.trust_forwarded_for is True


# --- DCR: stored display fields carry no control characters -------------------------------


@pytest.mark.parametrize("name", ["\x1b[31mops\x1b[0m", "a\nb", "tab\there", "del\x7f",
                                  "c1\x85", "csi\x9b31m", "a" * 101])
def test_register_rejects_control_characters_and_long_names(launch, name):
    status, body = register(Client(launch()), client_name=name)
    assert status == 400 and body["error"] == "invalid_client_metadata"


def test_register_rejects_control_characters_in_redirect_uris(launch):
    status, body = register(Client(launch()),
                            redirect_uris=["http://127.0.0.1:1/cb\x1b[2J"])
    assert status == 400 and body["error"] in ("invalid_redirect_uri",
                                               "invalid_client_metadata")


def test_register_accepts_a_plain_unicode_name_up_to_the_cap(launch):
    status, body = register(Client(launch()), client_name="Clàude Désktop " + "a" * 85)
    assert status == 201 and len(body["client_name"]) == 100


# --- (4) /token authenticates the client before taking the write lock ---------------------


def _count_transactions(provider, monkeypatch):
    opened = []
    original = provider.store.transaction

    def spy():
        opened.append(1)
        return original()

    monkeypatch.setattr(provider.store, "transaction", spy)
    return opened


@pytest.mark.parametrize("auth", ["unknown", "bad_secret", "secret_on_public", "no_client"])
def test_token_refuses_bad_clients_without_a_write_transaction(launch, monkeypatch, auth):
    from _mcp_http_helpers import basic

    httpd = launch()
    record, secret = httpd.oauth.create_client("svc")
    public = register(Client(httpd))[1]["client_id"]
    headers = {
        "unknown": {"Authorization": basic("nvcl_nope", "x")},
        "bad_secret": {"Authorization": basic(record["client_id"], secret + "x")},
        "secret_on_public": {"Authorization": basic(public, "x")},
        "no_client": {},
    }[auth]
    opened = _count_transactions(httpd.oauth, monkeypatch)
    status, _, data = Client(httpd).form("/token", {"grant_type": "client_credentials"},
                                         headers)
    assert status == 401 and json.loads(data)["error"] == "invalid_client"
    assert opened == []


def test_token_for_a_good_client_still_writes(launch, monkeypatch):
    from _mcp_http_helpers import basic

    httpd = launch()
    record, secret = httpd.oauth.create_client("svc")
    opened = _count_transactions(httpd.oauth, monkeypatch)
    status, _, data = Client(httpd).form("/token", {"grant_type": "client_credentials"},
                                         {"Authorization": basic(record["client_id"], secret)})
    assert status == 200 and json.loads(data)["access_token"]
    assert opened == [1]


def test_token_refuses_a_client_deleted_after_the_snapshot(launch, monkeypatch):
    # Authenticated against the snapshot, then deleted before the lock: the
    # transaction re-checks, so no token is issued for a client that is gone.
    from _mcp_http_helpers import basic

    httpd = launch()
    provider = httpd.oauth
    record, secret = provider.create_client("svc")
    original = provider.store.transaction

    def delete_first():
        provider.store.transaction = original
        provider.delete_client(record["client_id"])
        return original()

    monkeypatch.setattr(provider.store, "transaction", delete_first)
    status, _, data = Client(httpd).form("/token", {"grant_type": "client_credentials"},
                                         {"Authorization": basic(record["client_id"], secret)})
    assert status == 401


# --- (5) security headers -----------------------------------------------------------------

HSTS = "max-age=31536000; includeSubDomains"


@pytest.mark.parametrize("method, path", [("GET", "/healthz"), ("GET", "/nope"),
                                          ("POST", "/mcp"), ("GET", "/icon.svg"),
                                          ("OPTIONS", "/mcp"),
                                          ("GET", "/.well-known/oauth-protected-resource")])
def test_every_response_is_nosniff_and_hsts_only_on_an_https_public_url(launch, method, path):
    plain = Client(launch())
    status, headers, _ = plain.request(method, path, {} if method == "POST" else None)
    assert headers["X-Content-Type-Options"] == "nosniff", (status, path)
    assert "Strict-Transport-Security" not in headers

    https = Client(launch(public_url="https://mcp.example.test"))
    _, headers, _ = https.request(method, path, {} if method == "POST" else None)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Strict-Transport-Security"] == HSTS


def test_sse_stream_carries_the_security_headers(launch):
    httpd = launch()
    alice = authed(httpd)
    stream, resp = alice.open_stream("/sse")
    try:
        assert resp.status == 200
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
    finally:
        stream.close()


# --- (6) the container runs as a non-root user ---------------------------------------------


def _dockerfile_instructions():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    text = text.replace("\\\n", " ")  # join continuation lines
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def test_dockerfile_runs_as_a_non_root_system_user():
    lines = _dockerfile_instructions()
    users = [line.split(None, 1)[1].strip() for line in lines if line.startswith("USER ")]
    assert users, "no USER instruction: the server would run as root"
    user = users[-1].split(":")[0]
    assert user not in ("root", "0")
    run = " ".join(line for line in lines if line.startswith("RUN "))
    assert "useradd" in run and "--system" in run and user in run
    # The file-store fallback dirs stay writable for that user.
    assert f"chown -R {user}" in run and "/data" in run
    # USER comes after the last RUN, so the CMD (and nothing after) runs as root.
    last_run = max(i for i, line in enumerate(lines) if line.startswith("RUN "))
    last_user = max(i for i, line in enumerate(lines) if line.startswith("USER "))
    assert last_user > last_run
    cmd = next(i for i, line in enumerate(lines) if line.startswith("CMD "))
    assert last_user < cmd


def test_dockerfile_does_not_trust_forwarded_for_by_default():
    # A plain `docker run -p` exposes the server with no proxy in front, where
    # X-Forwarded-For is the caller's to forge; the platform sets it instead.
    assert "NUVEL_MCP_TRUST_FORWARDED_FOR" not in " ".join(_dockerfile_instructions())


# --- (3) URL-key expiry ------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


def test_url_key_with_expiry_is_refused_once_expired(launch):
    httpd = launch()
    clock = _Clock()
    httpd.oauth.clock = clock
    record, key = httpd.oauth.create_client("tester", url_key=True, expires_in=3600)
    assert record["url_key_expires_at"] == pytest.approx(clock.now + 3600)
    client = Client(httpd)
    assert client.post(INIT, path=f"/mcp/k/{key}")[0] == 200
    assert httpd.oauth.validate_url_key(key).expires_at == pytest.approx(clock.now + 3600)
    clock.now += 3601
    assert httpd.oauth.validate_url_key(key) is None
    status, headers, _ = client.post(INIT, path=f"/mcp/k/{key}")
    assert status == 401 and "resource_metadata" not in headers["WWW-Authenticate"]


def test_url_key_without_expiry_keeps_working(launch):
    httpd = launch()
    clock = _Clock()
    httpd.oauth.clock = clock
    _, key = httpd.oauth.create_client("tester", url_key=True)
    clock.now += 10 * 365 * 24 * 3600
    assert httpd.oauth.validate_url_key(key) is not None
    assert Client(httpd).post(INIT, path=f"/mcp/k/{key}")[0] == 200


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_url_key_expiry_must_be_positive_and_finite(store_path, bad):
    with pytest.raises(ValueError):
        make_provider(store_path).create_client("t", url_key=True, expires_in=bad)


def test_expiry_needs_a_url_key(store_path):
    with pytest.raises(ValueError):
        make_provider(store_path).create_client("t", expires_in=60)


@pytest.mark.file_store
def test_cli_url_key_expires_in_days(store_path, monkeypatch, capsys):
    from nuvel_mcp.cli import main as cli_main

    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    before = time.time()
    assert cli_main(["client", "create", "--name", "t", "--url-key", "--expires-in", "7"]) == 0
    out = json.loads(capsys.readouterr().out)
    expires = out["expires_at"]
    from datetime import datetime
    stamp = datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
    assert before + 7 * 86400 - 5 <= stamp <= time.time() + 7 * 86400 + 5
    provider = make_provider(store_path)
    assert provider.validate_url_key(out["url_key"]).expires_at == pytest.approx(stamp, abs=1)


@pytest.mark.file_store
@pytest.mark.parametrize("args", [["--url-key", "--expires-in", "0"],
                                  ["--url-key", "--expires-in", "-3"],
                                  ["--expires-in", "7"]])
def test_cli_rejects_bad_expires_in(store_path, monkeypatch, capsys, args):
    from nuvel_mcp.cli import main as cli_main

    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(store_path))
    assert cli_main(["client", "create", "--name", "t", *args]) == 2
    assert "--expires-in" in capsys.readouterr().err
