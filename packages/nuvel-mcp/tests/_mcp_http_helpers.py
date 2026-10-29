"""Shared helpers for the Skills MCP HTTP and OAuth tests.

Servers are real: bound on 127.0.0.1:0 and run in a background thread, driven
with ``http.client`` — no mocks of the HTTP layer.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import secrets
import threading
import urllib.parse
from pathlib import Path

from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.oauth import OAuthConfig, OAuthProvider
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader

TIMEOUT = 5
REDIRECT = "http://127.0.0.1:33418/callback"

HUB = {
    "software-development": [
        ("installer-transaction-design",
         "Plan/apply/doctor contract for installers that write agent config"),
        ("rollback", "Undo a bad deploy"),
    ],
    "hr": [("onboarding", "Employee onboarding checklist")],
}
INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "pytest", "version": "0"}},
}
TOOLS = ("search_skills", "list_skills", "list_themes", "get_skill", "install_skill",
         "fence_value", "propose_improvement", "record_feedback", "check_skill_health",
         "record_run_trace")

# Env that must not leak in from the developer's shell.
ISOLATED_VARS = (
    "GITHUB_TOKEN", "NUVEL_FEEDBACK_URL", "NUVEL_FEEDBACK_QUEUE", "NUVEL_SKILLS_INSTALL_DIR",
    "NUVEL_MCP_TOKEN", "NUVEL_MCP_ALLOWED_ORIGINS", "NUVEL_MCP_MAX_BODY_BYTES",
    "NUVEL_MCP_MAX_SESSIONS", "NUVEL_MCP_SESSION_TTL", "NUVEL_MCP_PUBLIC_URL",
    "NUVEL_MCP_ACCESS_TOKEN", "NUVEL_OAUTH_GITHUB_CLIENT_ID", "NUVEL_OAUTH_GITHUB_CLIENT_SECRET",
    "NUVEL_OAUTH_DEV_LOGIN", "NUVEL_OAUTH_ALLOWED_USERS", "NUVEL_OAUTH_REDIRECT_ALLOWLIST",
    "NUVEL_OAUTH_LOGIN",
)


def isolate_env(monkeypatch, tmp_path: Path) -> None:
    for var in ISOLATED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path / "feedback"))
    monkeypatch.setenv("NUVEL_MCP_OAUTH_STORE", str(tmp_path / "oauth" / "oauth.json"))


def call(tool, arguments, msg_id=2):
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}


def make_hub(root: Path) -> Path:
    skills = root / "skills"
    themes = {}
    for theme, skills_list in HUB.items():
        themes[theme] = []
        for name, description in skills_list:
            skill_dir = skills / theme / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\nversion: 1.0.0\n---\n\n{description}.\n",
                encoding="utf-8",
            )
            themes[theme].append({"name": name, "description": description,
                                  "path": f"skills/{theme}/{name}/SKILL.md"})
    (skills / "index.json").write_text(json.dumps({"themes": themes}), encoding="utf-8")
    return skills


def make_provider(store_path: Path, **overrides) -> OAuthProvider:
    return OAuthProvider(OAuthConfig(store_path=store_path, **{"dev_login": True, **overrides}))


def start(hub, provider: OAuthProvider | None = None, no_auth: bool = False,
          tools: frozenset[str] | None = None, trace_store=None, **config):
    """Bind and run a server; ``hub=None`` is feedback-only mode, ``tools`` the
    explicit allow-list (``--tools``), ``trace_store`` a ``--trace-store`` backend."""
    loader = SkillsLoader.from_base(hub) if hub is not None else None
    mcp = SkillsMCPServer(loader, tools=tools, trace_store=trace_store)
    cfg = HttpConfig(**{"ping_interval": 0.2, **config})
    httpd = make_server(mcp, "127.0.0.1", 0, cfg, provider, no_auth=no_auth)
    thread = threading.Thread(target=httpd.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    return httpd, thread


def basic(client_id: str, secret: str) -> str:
    raw = f"{urllib.parse.quote_plus(client_id)}:{urllib.parse.quote_plus(secret)}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def service_token(httpd, scope: str | None = None) -> str:
    """A client_credentials access token for a fresh operator-issued client."""
    provider = httpd.oauth
    record, secret = provider.create_client("pytest-service")
    form = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    return provider.token(form, basic(record["client_id"], secret), httpd.url)["access_token"]


class Client:
    """Tiny http.client wrapper bound to one running server; sends ``token``
    as a bearer on every request unless the caller sets Authorization."""

    def __init__(self, httpd, token: str | None = None):
        self.httpd = httpd
        self.host, self.port = httpd.server_address[:2]
        self.token = token

    @property
    def principal(self):
        if self.token is None:
            return None
        return self.httpd.oauth.validate(self.token, None).principal

    def _auth(self, headers: dict) -> dict:
        if self.token and "Authorization" not in headers:
            headers = {**headers, "Authorization": f"Bearer {self.token}"}
        return {k: v for k, v in headers.items() if v is not None}

    def conn(self):
        return http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT)

    def request(self, method, path, body=None, headers=None):
        conn = self.conn()
        if body is not None and not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        hdrs = self._auth({"Content-Type": "application/json",
                           "Accept": "application/json, text/event-stream", **(headers or {})})
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, resp.headers, data

    def post(self, body, session=None, headers=None, path="/mcp"):
        hdrs = dict(headers or {})
        if session:
            hdrs["Mcp-Session-Id"] = session
        return self.request("POST", path, body, hdrs)

    def form(self, path, fields: dict, headers=None):
        body = urllib.parse.urlencode(fields)
        return self.request("POST", path, body, {
            "Content-Type": "application/x-www-form-urlencoded", **(headers or {})})

    def initialize(self):
        status, headers, data = self.post(INIT)
        assert status == 200, data
        return headers["Mcp-Session-Id"]

    def open_stream(self, path, headers=None):
        conn = self.conn()
        conn.request("GET", path, headers=self._auth(
            {"Accept": "text/event-stream", **(headers or {})}))
        resp = conn.getresponse()
        return Stream(conn, resp), resp


class Stream:
    """Holds an SSE connection. ``close`` must close the response too: for a
    ``Connection: close`` reply, http.client hands the socket to the response,
    so closing only the connection leaves it open and the server never sees a
    hang-up."""

    def __init__(self, conn, resp):
        self.conn = conn
        self.resp = resp

    def close(self):
        self.resp.close()
        self.conn.close()


def read_frame(resp):
    """Read one SSE frame (up to a blank line) -> dict(event, data, comments)."""
    frame = {"event": None, "data": [], "comments": []}
    while True:
        raw = resp.readline()
        if not raw:
            raise EOFError("SSE stream closed")
        line = raw.decode("utf-8").rstrip("\r\n")
        if line == "":
            if frame["event"] or frame["data"] or frame["comments"]:
                return frame
            continue
        if line.startswith(":"):
            frame["comments"].append(line[1:].strip())
        else:
            key, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if key == "data":
                frame["data"].append(value)
            elif key == "event":
                frame["event"] = value


def read_event(resp, event="message"):
    """Skip comment frames until an ``event`` frame arrives; return its data."""
    while True:
        frame = read_frame(resp)
        if frame["event"] == event:
            return "\n".join(frame["data"])


# --- OAuth flow helpers ---


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


def refresh(client, client_id, refresh_token):
    status, _, data = client.form("/token", {"grant_type": "refresh_token",
                                             "refresh_token": refresh_token,
                                             "client_id": client_id})
    return status, json.loads(data)


def client_credentials(client, client_id, secret, post=False, **extra):
    fields = {"grant_type": "client_credentials", **extra}
    headers = {}
    if post:
        fields.update(client_id=client_id, client_secret=secret)
    else:
        headers["Authorization"] = basic(client_id, secret)
    status, _, data = client.form("/token", fields, headers)
    return status, json.loads(data)
