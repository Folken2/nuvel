"""Shared helpers for the Skills MCP HTTP and OAuth tests.

Servers are real: bound on 127.0.0.1:0 and run in a background thread, driven
with ``http.client`` — no mocks of the HTTP layer.
"""

from __future__ import annotations

import base64
import http.client
import json
import threading
import urllib.parse
from pathlib import Path

from nuvel_mcp.http_transport import HttpConfig, make_server
from nuvel_mcp.oauth import OAuthConfig, OAuthProvider
from nuvel_mcp.server import SkillsMCPServer
from nuvel_mcp.skills_loader import SkillsLoader

TIMEOUT = 5

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
          tools: frozenset[str] | None = None, **config):
    """Bind and run a server; ``hub=None`` is feedback-only mode, ``tools`` the
    explicit allow-list (``--tools``)."""
    loader = SkillsLoader.from_base(hub) if hub is not None else None
    mcp = SkillsMCPServer(loader, tools=tools)
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
