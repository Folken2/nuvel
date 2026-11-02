"""HTTP transports for the Nuvel Skills MCP server. Stdlib only.

Serves the same :class:`~nuvel_mcp.server.SkillsMCPServer` dispatcher that the
stdio loop uses, over two wire formats:

Streamable HTTP (MCP 2025-03-26), single endpoint ``/mcp``:
    POST   /mcp   JSON-RPC request(s) -> application/json, or text/event-stream
                  when the client's Accept prefers it. ``initialize`` mints an
                  ``Mcp-Session-Id``; every later request must echo it.
    GET    /mcp   SSE stream for server -> client messages.
    DELETE /mcp   Terminate the session.

Legacy HTTP+SSE (MCP 2024-11-05):
    GET  /sse                        SSE stream; first event is ``endpoint``.
    POST /messages?sessionId=<id>    JSON-RPC in, ``202``; replies go to the stream.

Plus ``GET /healthz`` (unauthenticated liveness + counts only) and the public
brand icons ``GET /favicon.ico``, ``/icon.png`` and ``/icon.svg``.

Every MCP method on both transports requires an OAuth 2.1 bearer access token
(see :mod:`nuvel_mcp.oauth`); this server is also the authorization server:

    GET  /.well-known/oauth-protected-resource     RFC 9728 metadata
    GET  /.well-known/oauth-authorization-server   RFC 8414 metadata
    POST /register                                 RFC 7591 dynamic registration
    GET  /authorize, POST /authorize               consent page -> login
    GET  /oauth/callback                           GitHub login callback
    POST /oauth/dev-login                          NUVEL_OAUTH_DEV_LOGIN=1 only
    POST /token                                    code / refresh / client_credentials

An unauthenticated request gets ``401`` with
``WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"``.

``/mcp/k/<key>`` is the Streamable HTTP endpoint for clients that can only be
given a URL: the operator-issued URL key in the path takes the place of the
bearer token (see :meth:`OAuthProvider.validate_url_key`). The legacy SSE
transport has no key form. With ``NUVEL_OAUTH_LOGIN=none`` the interactive
OAuth routes (/register, /authorize and the login callbacks) are not served.
``make_server(..., no_auth=True)`` (``--no-auth``) turns all of this off for
loopback development.

Every server built here runs its dispatcher in remote mode
(``SkillsMCPServer.enable_remote_mode``): nothing read from the feedback store
is served, and without an explicit ``tools`` allow-list only
``HTTP_DEFAULT_TOOLS`` are exposed. Each authenticated dispatch runs with
``nuvel_mcp.auth.current_principal`` set to the token's tenant
(``TokenInfo.tenant``: the ``client_id``, plus a hash of the user for an
interactive login), which run traces are stamped and scoped with (``None``
with no auth).

Diagnostics go to stderr; request bodies, query strings, URL keys, tokens and
codes are never logged.
"""

from __future__ import annotations

import contextlib
import http.cookies
import ipaddress
import json
import math
import os
import queue
import re
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nuvel_mcp import oauth
from nuvel_mcp.auth import current_principal
from nuvel_mcp.clientip import client_ip, trust_forwarded_for
from nuvel_mcp.oauth import (
    FORBIDDEN,
    UNAUTHORIZED,
    AuthorizeError,
    OAuthError,
    OAuthProvider,
    OAuthStoreError,
    TokenInfo,
)
from nuvel_mcp.server import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    PARSE_ERROR,
    SkillsMCPServer,
    brand_asset,
    error_response,
    log,
)
from nuvel_mcp.skills_loader import SkillsError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_SESSIONS = 64
DEFAULT_SESSION_TTL = 3600.0
DEFAULT_PING_INTERVAL = 15.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_REQUEST_DEADLINE = 30.0
DEFAULT_HEADER_DEADLINE = 5.0
DEFAULT_MAX_CONNECTIONS = 128
DEFAULT_MAX_SESSIONS_PER_PRINCIPAL = 8
DEFAULT_MAX_STREAMS_PER_PRINCIPAL = 4
DEFAULT_MAX_CONNECTIONS_PER_IP = 16
# Retry-After on a per-caller 429: a slot frees when the caller ends a session
# or stream, which the server cannot predict; a few seconds avoids a hot loop.
PER_CALLER_RETRY_AFTER = 5
CORS_MAX_AGE = 600

SESSION_HEADER = "Mcp-Session-Id"
# JSON-RPC error codes for transport-level failures (implementation-defined range).
SESSION_NOT_FOUND = -32001
SERVER_BUSY = -32003

_STREAM_CLOSED = object()  # queue sentinel: end the SSE stream
_QUERY = re.compile(r"\?\S*")
# A URL key: the segment after /k/ on any path, and any nvuk_ run wherever it
# turns up (a key pasted into the wrong place).
_KEY_SEGMENT = re.compile(r"(/k/)[^/?\s]+")
_KEY_RUN = re.compile(re.escape(oauth.URL_KEY_PREFIX) + r"[A-Za-z0-9_\-]*")
_HOST_HEADER = re.compile(r"[A-Za-z0-9.\-]+(:\d+)?|\[[0-9A-Fa-f:.]+\](:\d+)?")


# --- Config / helpers -------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


@dataclass
class HttpConfig:
    """Tunables for the HTTP transport (see :meth:`from_env`)."""

    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_ttl: float = DEFAULT_SESSION_TTL
    ping_interval: float = DEFAULT_PING_INTERVAL
    # Seconds one socket read (or write) may stall; also how long an idle
    # keep-alive connection is kept. Must be > 0.
    read_timeout: float = DEFAULT_READ_TIMEOUT
    # Wall-clock seconds a client gets to send a whole request (request line,
    # headers and body), however slowly it trickles. Must be > 0.
    request_deadline: float = DEFAULT_REQUEST_DEADLINE
    # Behind a trusted proxy only: seconds a connection gets to finish its
    # request line and headers (idle keep-alive included). Until then it has
    # no caller IP to count against the per-IP cap, so this bounds how long an
    # unidentified connection can sit on a global slot. Must be > 0.
    header_deadline: float = DEFAULT_HEADER_DEADLINE
    # Handler threads (open connections, SSE streams included) served at once;
    # further connections are refused with 503. Must be >= 1.
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    # Per principal (a signed-in user's subject across all their clients; else
    # the client: client_credentials, URL keys -- see limit_key): sessions open
    # and SSE streams (GET /mcp and GET /sse) held at once, so one caller
    # cannot take every global slot. Over either cap is 429. Must be >= 1.
    max_sessions_per_principal: int = DEFAULT_MAX_SESSIONS_PER_PRINCIPAL
    max_streams_per_principal: int = DEFAULT_MAX_STREAMS_PER_PRINCIPAL
    # Per caller IP (see trust_forwarded_for): connections held at once,
    # refused with 429 from the accept loop. Behind a trusted proxy every TCP
    # peer is the proxy, so the cap counts in-flight requests (SSE streams
    # included) by X-Forwarded-For instead. Must be >= 1.
    max_connections_per_ip: int = DEFAULT_MAX_CONNECTIONS_PER_IP
    # NUVEL_MCP_TRUST_FORWARDED_FOR: take the caller's IP from the right-most
    # X-Forwarded-For entry (our proxy's). Only behind a proxy that sets it,
    # or callers could pick their own IP.
    trust_forwarded_for: bool = False
    allowed_origins: frozenset[str] = field(default_factory=frozenset)
    # The origin clients reach this server at (NUVEL_MCP_PUBLIC_URL). It is the
    # OAuth issuer and resource; unset, the origin is derived from Host.
    public_url: str | None = None

    def __post_init__(self) -> None:
        for name in ("read_timeout", "request_deadline", "header_deadline"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                env = "NUVEL_MCP_" + name.upper()
                raise ValueError(f"{env} must be a number of seconds greater than 0 (got {value})")
        for name in ("max_connections", "max_sessions_per_principal",
                     "max_streams_per_principal", "max_connections_per_ip"):
            value = getattr(self, name)
            if value < 1:
                raise ValueError(f"NUVEL_MCP_{name.upper()} must be at least 1 (got {value})")

    @classmethod
    def from_env(cls) -> "HttpConfig":
        origins = os.environ.get("NUVEL_MCP_ALLOWED_ORIGINS", "")
        return cls(
            max_body_bytes=_env_int("NUVEL_MCP_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
            max_sessions=_env_int("NUVEL_MCP_MAX_SESSIONS", DEFAULT_MAX_SESSIONS),
            session_ttl=_env_float("NUVEL_MCP_SESSION_TTL", DEFAULT_SESSION_TTL),
            read_timeout=_env_float("NUVEL_MCP_READ_TIMEOUT", DEFAULT_READ_TIMEOUT),
            request_deadline=_env_float("NUVEL_MCP_REQUEST_DEADLINE", DEFAULT_REQUEST_DEADLINE),
            header_deadline=_env_float("NUVEL_MCP_HEADER_DEADLINE", DEFAULT_HEADER_DEADLINE),
            max_connections=_env_int("NUVEL_MCP_MAX_CONNECTIONS", DEFAULT_MAX_CONNECTIONS),
            max_sessions_per_principal=_env_int("NUVEL_MCP_MAX_SESSIONS_PER_PRINCIPAL",
                                                DEFAULT_MAX_SESSIONS_PER_PRINCIPAL),
            max_streams_per_principal=_env_int("NUVEL_MCP_MAX_STREAMS_PER_PRINCIPAL",
                                               DEFAULT_MAX_STREAMS_PER_PRINCIPAL),
            max_connections_per_ip=_env_int("NUVEL_MCP_MAX_CONNECTIONS_PER_IP",
                                            DEFAULT_MAX_CONNECTIONS_PER_IP),
            trust_forwarded_for=trust_forwarded_for(),
            allowed_origins=frozenset(
                o for o in (_normalize_origin(x) for x in origins.split(",")) if o
            ),
            public_url=oauth.public_url_from_env(),
        )


def _normalize_origin(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def _redact(text: str) -> str:
    """Strip what must never reach a log: query strings and URL keys."""
    text = _KEY_SEGMENT.sub(r"\1<redacted>", text)
    text = _KEY_RUN.sub("<redacted>", text)
    return _QUERY.sub("?<redacted>", text)


def is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and loopback IP literals (``127.0.0.0/8``, ``::1``)."""
    host = host.strip().strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # other hostnames: treat as remote


def origin_allowed(origin: str, allowed: frozenset[str]) -> bool:
    """Loopback origins always; otherwise only exact entries in ``allowed``."""
    parts = urllib.parse.urlsplit(origin.strip())
    if parts.scheme in ("http", "https") and parts.hostname and is_loopback_host(parts.hostname):
        return True
    return _normalize_origin(origin) in allowed


def _accept_q(accept: str) -> dict[str, float]:
    """Parse an Accept header into ``{media_range: q}``."""
    prefs: dict[str, float] = {}
    for part in accept.split(","):
        media, *params = (p.strip() for p in part.split(";"))
        if not media:
            continue
        q = 1.0
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    q = float(value)
                except ValueError:
                    q = 0.0
        prefs[media.lower()] = max(q, prefs.get(media.lower(), 0.0))
    return prefs


def prefers_sse(accept: str | None) -> bool:
    """True when Accept ranks text/event-stream strictly above application/json."""
    if not accept:
        return False
    prefs = _accept_q(accept)
    sse = prefs.get("text/event-stream", 0.0)
    json_q = prefs.get(
        "application/json", prefs.get("application/*", prefs.get("*/*", 0.0))
    )
    return sse > json_q


def accepts_sse(accept: str | None) -> bool:
    return _accept_q(accept or "").get("text/event-stream", 0.0) > 0


def sse_event(data, event: str = "message") -> bytes:
    """Frame one SSE event. ``data`` is JSON-encoded unless already a string."""
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False)
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    return f"event: {event}\n{lines}\n".encode("utf-8")


# --- Sessions ------------------------------------------------------------------------


class Session:
    def __init__(self, session_id: str, kind: str, principal=None):
        self.id = session_id
        self.kind = kind  # "streamable" | "legacy"
        # (client_id, subject) of the token that opened it; only that
        # principal may use the session. None when auth is off.
        self.principal = principal
        self.last_seen = time.monotonic()
        self.outbox: queue.Queue = queue.Queue()
        self.closed = threading.Event()
        # Held by the one GET /mcp stream allowed per streamable session, so a
        # single session cannot pin every connection slot with parallel streams.
        self.stream_lock = threading.Lock()

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def send(self, message) -> None:
        """Queue a server -> client JSON-RPC message for this session's SSE stream."""
        if not self.closed.is_set():
            self.outbox.put(message)

    def close(self) -> None:
        if not self.closed.is_set():
            self.closed.set()
            self.outbox.put(_STREAM_CLOSED)


class SessionFull(Exception):
    pass


def limit_key(principal):
    """What the per-principal caps count against: a signed-in user's subject
    alone (one user who registers more clients through DCR gets no more
    sessions or streams), else the client (client_credentials and URL keys,
    whose subject is ``client:<client_id>``). None when auth is off."""
    if principal is None:
        return None
    client_id, subject = principal
    if not subject or subject == f"client:{client_id}":
        return ("client", client_id)
    return ("subject", oauth.subject_identity(subject))


class PrincipalSessionsFull(Exception):
    """This principal already holds its share of sessions (the server may not be full)."""


class KeyedLimiter:
    """Concurrent holders per key (a principal, an IP), at most ``limit`` each.

    ``acquire`` never blocks: a caller over its share is refused at once.
    Keys with no holders are dropped, so the table only holds live callers.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self._held: dict = {}
        self._lock = threading.Lock()

    def acquire(self, key) -> bool:
        with self._lock:
            held = self._held.get(key, 0)
            if held >= self.limit:
                return False
            self._held[key] = held + 1
            return True

    def release(self, key) -> None:
        with self._lock:
            held = self._held.get(key, 0) - 1
            if held > 0:
                self._held[key] = held
            else:
                self._held.pop(key, None)


class SessionStore:
    """Thread-safe session table with a size cap, a per-principal cap and
    idle expiry. The per-principal cap is skipped when auth is off (principal
    ``None``): there is only the one local caller."""

    def __init__(self, max_sessions: int, ttl: float, max_per_principal: int | None = None):
        self.max_sessions = max_sessions
        self.max_per_principal = max_per_principal
        self.ttl = ttl
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _reap_locked(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for sid in [s.id for s in self._sessions.values() if s.last_seen < cutoff]:
            self._sessions.pop(sid).close()

    def create(self, kind: str, principal=None) -> Session:
        with self._lock:
            self._reap_locked()
            if len(self._sessions) >= self.max_sessions:
                raise SessionFull()
            key = limit_key(principal)
            if key is not None and self.max_per_principal is not None and sum(
                    limit_key(s.principal) == key for s in self._sessions.values()
            ) >= self.max_per_principal:
                raise PrincipalSessionsFull()
            session = Session(secrets.token_urlsafe(32), kind, principal)
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str | None, kind: str, principal=None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            self._reap_locked()
            session = self._sessions.get(session_id)
            if session is None or session.kind != kind or session.principal != principal:
                return None
            session.touch()
            return session

    def terminate(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.close()

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


# --- HTTP server -----------------------------------------------------------------------


class _DeadlineReader:
    """Request-side reader with a wall-clock deadline.

    Replaces ``rfile``: every ``recv`` runs with the time left until the
    deadline as its socket timeout, so a client trickling bytes cannot hold the
    handler thread past it. Between reads the socket timeout is ``idle``
    (which also bounds writes). The leftover buffer carries over to the next
    keep-alive request.
    """

    def __init__(self, sock: socket.socket, idle: float):
        self._sock = sock
        self._idle = idle
        self._buf = bytearray()
        self._deadline = math.inf

    def arm(self, seconds: float) -> None:
        self._deadline = time.monotonic() + seconds

    def _fill(self) -> bool:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("request deadline exceeded")
        self._sock.settimeout(min(remaining, self._idle))
        try:
            data = self._sock.recv(65536)
        except TimeoutError:
            raise
        except OSError:  # reset by peer: same as EOF
            data = b""
        finally:
            self._sock.settimeout(self._idle)
        self._buf += data
        return bool(data)

    def readline(self, limit: int = -1) -> bytes:
        start = 0
        while True:
            end = self._buf.find(b"\n", start)
            if end >= 0:
                end += 1
                break
            if 0 <= limit <= len(self._buf):
                end = limit
                break
            start = len(self._buf)
            if not self._fill():
                end = len(self._buf)
                break
        if limit >= 0:
            end = min(end, limit)
        line = bytes(self._buf[:end])
        del self._buf[:end]
        return line

    def read(self, size: int) -> bytes:
        while len(self._buf) < size and self._fill():
            pass
        data = bytes(self._buf[:size])
        del self._buf[:size]
        return data

    def close(self) -> None:
        pass


# Upper bound on the bytes a refused (503) connection's unread request may be
# drained of before closing; the drain itself never waits for more.
_DRAIN_LIMIT = 256 * 1024


def _drain_pending(sock: socket.socket) -> None:
    """Read and discard whatever request bytes are already queued on ``sock``.

    Closing a TCP socket with unread data sends a reset, which can make the
    client drop the response it was just sent; draining first gives it a clean
    close. Non-blocking and bounded, so it cannot stall the accept loop.
    """
    sock.setblocking(False)
    drained = 0
    try:
        while drained < _DRAIN_LIMIT:
            data = sock.recv(65536)
            if not data:
                break
            drained += len(data)
    except (BlockingIOError, InterruptedError):
        pass


class McpHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying the MCP dispatcher, config, sessions and
    the OAuth provider (``None`` only when auth is deliberately off)."""

    allow_reuse_address = True  # SO_REUSEADDR
    daemon_threads = True

    def __init__(self, address, mcp: SkillsMCPServer, config: HttpConfig,
                 oauth_provider: OAuthProvider | None):
        host = address[0]
        if ":" in host.strip("[]"):
            self.address_family = socket.AF_INET6
            address = (host.strip("[]"), address[1])
        # Every HTTP server runs in remote mode with the gated default tool
        # set (unless the caller chose an explicit allow-list).
        mcp.enable_remote_mode()
        mcp.transport = "http"
        self.mcp = mcp
        self.config = config
        self.oauth = oauth_provider
        self.sessions = SessionStore(config.max_sessions, config.session_ttl,
                                     config.max_sessions_per_principal)
        # Open SSE streams per principal (GET /mcp and GET /sse alike).
        self.principal_streams = KeyedLimiter(config.max_streams_per_principal)
        self.stopping = threading.Event()
        self._slots = threading.BoundedSemaphore(config.max_connections)
        # Per caller IP: connections by TCP peer here in the accept loop, or,
        # behind a trusted proxy, in-flight requests by X-Forwarded-For (see
        # McpRequestHandler._ip_slot).
        self.per_ip = KeyedLimiter(config.max_connections_per_ip)
        super().__init__(address, McpRequestHandler)

    def _peer_key(self, client_address) -> str | None:
        """The per-IP key counted at accept time, or None behind a trusted
        proxy (where the peer is the proxy, shared by every caller)."""
        return None if self.config.trust_forwarded_for else client_address[0]

    def _refuse(self, request, status: bytes) -> None:
        """Answer from the accept loop (one short write) without a thread."""
        try:
            request.settimeout(1.0)
            request.sendall(b"HTTP/1.1 " + status + b"\r\nRetry-After: 1\r\n"
                            b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            request.shutdown(socket.SHUT_WR)
            _drain_pending(request)
        except OSError:
            pass
        self.shutdown_request(request)

    def process_request(self, request, client_address) -> None:
        # Cap handler threads: when full, answer 503 without spawning one.
        if not self._slots.acquire(blocking=False):
            self._refuse(request, b"503 Service Unavailable")
            return
        # One peer over its share: 429, as cheaply.
        peer = self._peer_key(client_address)
        if peer is not None and not self.per_ip.acquire(peer):
            self._slots.release()
            self._refuse(request, b"429 Too Many Requests")
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            if peer is not None:
                self.per_ip.release(peer)
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            peer = self._peer_key(client_address)
            if peer is not None:
                self.per_ip.release(peer)
            self._slots.release()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        host = f"[{host}]" if ":" in host else host
        return f"http://{host}:{port}"

    def stop(self) -> None:
        """Close every session (ending SSE streams), then stop serve_forever."""
        self.stopping.set()
        self.sessions.close_all()
        self.shutdown()


class _Reject(Exception):
    """Short-circuit a request with an HTTP error."""

    def __init__(self, status: int, message: str, code: int = INVALID_REQUEST,
                 headers: dict | None = None):
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers or {}


_HTML_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": ("default-src 'none'; style-src 'unsafe-inline'; "
                                "frame-ancestors 'none'; base-uri 'none'"),
    "Referrer-Policy": "no-referrer",
}
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# Public brand files: request path -> (file in nuvel_mcp/brand/, Content-Type).
_BRAND_FILES = {
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/icon.png": ("icon.png", "image/png"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


# RFC 9110 field-name: a token.
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


class _HeaderTap:
    """Records the raw lines read while the header block is parsed: the parser
    unfolds obs-fold, so a fold after an empty value would otherwise vanish."""

    def __init__(self, fp) -> None:
        self._fp = fp
        self.lines: list[bytes] = []

    def readline(self, *args) -> bytes:
        line = self._fp.readline(*args)
        self.lines.append(line)
        return line


class McpRequestHandler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        # Reads go through a wall-clock deadline (see handle_one_request); SSE
        # streams only write, so they are unaffected.
        self.connection.settimeout(self.server.config.read_timeout)
        self.rfile.close()
        self.rfile = _DeadlineReader(self.connection, self.server.config.read_timeout)

    def handle_one_request(self) -> None:
        # One deadline for request line + headers + body (an idle keep-alive
        # connection counts too). Expiry closes the connection quietly; a body
        # that outlives it gets 408 in _read_body.
        config = self.server.config
        self._request_started = time.monotonic()
        if config.trust_forwarded_for:
            # Behind a proxy the headers name the caller (X-Forwarded-For);
            # until they arrive the connection counts against no per-IP share,
            # so it gets only the short header deadline (see parse_request).
            self.rfile.arm(min(config.header_deadline, config.request_deadline))
        else:
            self.rfile.arm(config.request_deadline)
        super().handle_one_request()

    def parse_request(self) -> bool:
        self._expects_continue = False
        tap = _HeaderTap(self.rfile)
        self._raw_header_lines = tap.lines
        self.rfile = tap
        try:
            return super().parse_request()
        finally:
            self.rfile = tap._fp
            if self.server.config.trust_forwarded_for:
                # Identified now: the body gets what is left of the request deadline.
                self.rfile.arm(self.server.config.request_deadline
                               - (time.monotonic() - self._request_started))

    def handle_expect_100(self) -> bool:
        # The stdlib would send "100 Continue" here, inside parse_request, before
        # the framing gate, Origin and auth have run. Defer it to _read_body: the
        # interim response goes out only once the request is accepted and its
        # body is about to be read; a refused request gets its one final status.
        self._expects_continue = True
        return True

    server: McpHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "nuvel-skills-mcp"
    sys_version = ""
    _url_key: str | None = None  # set per request by _route on /mcp/k/<key>

    # --- plumbing ---------------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        # Request line + status only; query strings (session ids, OAuth codes and
        # state) and URL keys are redacted and bodies are never logged.
        args = tuple(_redact(a) if isinstance(a, str) else a for a in args)
        sys.stderr.write(f"{self.address_string()} - {format % args}\n")

    def _path(self) -> str:
        return urllib.parse.urlsplit(self.path).path

    def _query(self) -> dict[str, str]:
        parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        return {k: v[0] for k, v in parsed.items()}

    def _cors_headers(self) -> dict[str, str]:
        origin = self.headers.get("Origin")
        if not origin or not self._origin_ok(origin):
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Expose-Headers": f"{SESSION_HEADER}, WWW-Authenticate",
            "Vary": "Origin",
        }

    def _origin_ok(self, origin: str) -> bool:
        public = self.server.config.public_url
        if public and _normalize_origin(origin) == public:
            return True  # our own pages (the consent form posts back here)
        return origin_allowed(origin, self.server.config.allowed_origins)

    def _check_origin(self) -> None:
        """DNS-rebinding guard: a present Origin must be loopback, our public
        origin, or allow-listed.

        A request with no Origin passes even when its Host is foreign (a
        rebound name). That is acceptable because every path that dispatches
        or changes state (POST /mcp, POST /messages, DELETE /mcp and the OAuth
        POSTs) is a non-GET request, and browsers send Origin on every non-GET
        request, same-origin ones included -- which is what a rebinding page's
        requests are -- so they are caught here. A non-browser client that
        omits Origin is not a rebinding attack. GET paths are readable without
        Origin, but expose only counts (/healthz), public OAuth metadata and the
        login pages (whose state changes are POSTs); the streams (GET /mcp,
        GET /sse) need a bearer token (``--no-auth`` is loopback-only).
        """
        origin = self.headers.get("Origin")
        if origin and not self._origin_ok(origin):
            raise _Reject(HTTPStatus.FORBIDDEN, "Origin not allowed")

    def _security_headers(self) -> dict[str, str]:
        """On every response. HSTS only when the configured public URL is
        https: it is the one origin we know browsers reach over TLS (a
        loopback or plain-http deploy must not pin itself to https)."""
        headers = {"X-Content-Type-Options": "nosniff"}
        public = self.server.config.public_url
        if public and public.startswith("https://"):
            headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return headers

    def _send(self, status: int, body: bytes = b"", content_type: str | None = None,
              headers: dict | None = None) -> None:
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in {**self._security_headers(), **self._cors_headers(),
                           **(headers or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status: int, payload, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json", headers)

    def _send_html(self, status: int, body: bytes, headers: dict | None = None) -> None:
        self._send(status, body, "text/html; charset=utf-8", {**_HTML_HEADERS, **(headers or {})})

    def _redirect(self, location: str, headers: dict | None = None) -> None:
        self._send(HTTPStatus.FOUND, headers={"Location": location, **_NO_STORE, **(headers or {})})

    def _send_reject(self, exc: _Reject, msg_id=None) -> None:
        self._send_json(exc.status, error_response(msg_id, exc.code, exc.message), exc.headers)

    def _check_framing(self) -> int:
        """The one framing gate, run before Origin, auth and routing: return the
        declared body length or raise. Every refusal here leaves the connection
        marked to close (see ``_handle``), so a body is never misread as the
        next request.
        """
        # A header line the parser could not take as "token: value" (e.g.
        # whitespace before the colon) or a line starting with whitespace.
        if self.headers.defects:
            raise _Reject(HTTPStatus.BAD_REQUEST, "Invalid header")
        # obs-fold (a line starting with SP/HTAB), seen on the raw lines because
        # the parser joins a fold after an empty value into a clean one.
        if any(line[:1] in (b" ", b"\t") for line in self._raw_header_lines):
            raise _Reject(HTTPStatus.BAD_REQUEST, "Invalid header")
        for name, value in self.headers.items():
            if not _TOKEN.fullmatch(name) or any(c in value for c in "\r\n\x00"):
                raise _Reject(HTTPStatus.BAD_REQUEST, "Invalid header")
        # No transfer coding is supported, in any form or count.
        if "Transfer-Encoding" in self.headers:
            raise _Reject(HTTPStatus.LENGTH_REQUIRED, "Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            return 0
        # More than one Content-Length, equal or not, is refused (RFC 9112 6.3).
        # ASCII digits only: int() would also take "+1", "-0", "1_0" and
        # non-ASCII digits, none of which a valid Content-Length may be.
        # Only SP/HTAB is optional whitespace (RFC 9110 5.6.3); str.strip()
        # would also take VT, FF, NBSP, \x1c-\x1f and \x85.
        raw = lengths[0].strip(" \t")
        if len(lengths) > 1 or not (raw.isascii() and raw.isdigit()):
            raise _Reject(HTTPStatus.BAD_REQUEST, "Invalid Content-Length")
        # More digits than the cap has is over it; checked before int(), which
        # refuses strings past 4300 digits.
        max_body = self.server.config.max_body_bytes
        if len(raw) > len(str(max_body)) or int(raw) > max_body:
            raise _Reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
        return int(raw)

    def _read_body(self) -> bytes:
        # Framing was decided by _check_framing; this only reads what it allowed.
        length = self._body_length
        if length and self._expects_continue:
            self._expects_continue = False
            self.send_response_only(HTTPStatus.CONTINUE)
            self.end_headers()
        try:
            body = self.rfile.read(length) if length else b""
        except (TimeoutError, OSError):  # client stalled (socket.timeout) or vanished
            self.close_connection = True
            raise _Reject(HTTPStatus.REQUEST_TIMEOUT, "Request timed out")
        if len(body) < length:  # EOF before the declared length: never parse a partial body
            self.close_connection = True
            raise _Reject(HTTPStatus.BAD_REQUEST, "Incomplete request body")
        # The gate passed and the whole declared body was read: keep-alive is safe.
        self.close_connection = self._client_wants_close
        return body

    def _read_form(self) -> dict[str, str]:
        """An application/x-www-form-urlencoded body; repeated parameters are refused."""
        body = self._read_body()
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded":
            raise OAuthError("invalid_request",
                             "Content-Type must be application/x-www-form-urlencoded")
        try:
            pairs = urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True,
                                           max_num_fields=50)
        except (UnicodeDecodeError, ValueError):
            raise OAuthError("invalid_request", "Malformed form body")
        form: dict[str, str] = {}
        for key, value in pairs:
            if key in form:
                raise OAuthError("invalid_request", f"Parameter {key!r} repeated")
            form[key] = value
        return form

    def _parse_jsonrpc(self, body: bytes):
        """Decode a JSON-RPC payload -> (messages, is_batch)."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError) as exc:  # bad JSON or UTF-8, or nested too deep
            log(f"Parse error: {type(exc).__name__}")  # never the body
            raise _Reject(HTTPStatus.BAD_REQUEST, "Parse error", PARSE_ERROR)
        if isinstance(payload, list):
            if not payload:
                raise _Reject(HTTPStatus.BAD_REQUEST, "Invalid Request: empty batch")
            return payload, True
        return [payload], False

    # --- auth -----------------------------------------------------------------------

    def _origin(self) -> str:
        """This server's origin: NUVEL_MCP_PUBLIC_URL, else derived from Host."""
        if self.server.config.public_url:
            return self.server.config.public_url
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        if proto not in ("http", "https"):
            proto = "http"
        host = self.headers.get("Host") or ""
        if not _HOST_HEADER.fullmatch(host):
            host = self.server.url.split("://", 1)[1]
        return f"{proto}://{host.lower()}"

    def _resource_metadata_url(self) -> str:
        return f"{self._origin()}/.well-known/oauth-protected-resource"

    def _challenge(self, error: str | None = None, scope: str | None = None) -> dict:
        params = []
        if error:
            params.append(f'error="{error}"')
        params.append(f'resource_metadata="{self._resource_metadata_url()}"')
        if scope:
            params.append(f'scope="{scope}"')
        return {"WWW-Authenticate": "Bearer " + ", ".join(params)}

    def _bearer(self) -> str | None:
        if len(self.headers.get_all("Authorization") or []) > 1:
            return None  # ambiguous credentials: refuse before any token check
        scheme, _, presented = (self.headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            return None
        return presented.strip()

    def _authenticate(self) -> TokenInfo | None:
        """Require a valid access token, or URL key on a key path (None when
        auth is off).

        Every failure — missing, unknown, expired, revoked, another client's or
        another resource's token — is the same 401, so nothing leaks which.
        """
        provider = self.server.oauth
        if provider is None:
            return None
        if self._url_key is not None:
            return self._authenticate_url_key(provider)
        all_scopes = oauth.format_scope(oauth.SCOPES)
        token = self._bearer()
        if token is None:
            raise _Reject(HTTPStatus.UNAUTHORIZED, "Unauthorized: a bearer access token is required",
                          UNAUTHORIZED, self._challenge(scope=all_scopes))
        try:
            info = provider.validate(token, self._origin())
        except OAuthStoreError as exc:
            log(f"OAuth store error: {exc}")
            info = None  # fail closed
        if info is None:
            raise _Reject(HTTPStatus.UNAUTHORIZED, "Unauthorized: invalid or expired access token",
                          UNAUTHORIZED, self._challenge("invalid_token", all_scopes))
        if not provider.subject_allowed(info):
            raise _Reject(HTTPStatus.FORBIDDEN, "Forbidden", FORBIDDEN)
        return info

    def _authenticate_url_key(self, provider: OAuthProvider) -> TokenInfo:
        """A key path: the key is the only credential. An Authorization header
        alongside it is refused (one credential per request), and the 401 has
        no resource_metadata, since OAuth discovery cannot fix a bad key."""
        info = None
        if not self.headers.get_all("Authorization"):
            try:
                info = provider.validate_url_key(self._url_key)
            except OAuthStoreError as exc:
                log(f"OAuth store error: {exc}")  # fail closed
        if info is None:
            raise _Reject(HTTPStatus.UNAUTHORIZED, "Unauthorized: invalid URL key",
                          UNAUTHORIZED, {"WWW-Authenticate": 'Bearer error="invalid_token"'})
        return info

    def _check_scopes(self, info: TokenInfo | None, messages) -> None:
        if info is None:
            return
        for message in messages:
            scope = oauth.required_scope(message)
            if scope not in info.scope:
                raise _Reject(HTTPStatus.FORBIDDEN, f"Forbidden: requires scope {scope}",
                              FORBIDDEN, self._challenge("insufficient_scope", scope))

    def _principal(self, info: TokenInfo | None):
        return info.principal if info else None

    def _still_valid(self, info: TokenInfo | None):
        """For long-lived SSE streams: re-check the token on every ping."""
        if info is None:
            return lambda: True
        if self._url_key is not None:
            key = self._url_key
            return lambda: self.server.oauth.validate_url_key(key) is not None
        token, origin = self._bearer(), self._origin()
        return lambda: self.server.oauth.validate(token, origin) is not None

    # --- dispatch -------------------------------------------------------------------

    def _dispatch_all(self, messages, info: TokenInfo | None) -> list:
        """Run each message through the shared dispatcher; collect responses.

        The caller's tenant is ``TokenInfo.tenant``: the ``client_id`` for a
        client acting as itself (client_credentials, URL keys), the client_id
        plus a hash of the user for an interactive login (never the subject,
        a person's login, itself); ``None`` when auth is off. Dispatch is synchronous on
        this thread, so the tool handlers see it via ``current_principal``.
        This is the only place the principal is set for HTTP: both POST /mcp
        and the legacy POST /messages route through here, and the ``finally``
        resets it even when dispatch raises, so a keep-alive connection's next
        request never inherits it.
        """
        reset = current_principal.set(info.tenant if info else None)
        try:
            return self._dispatch_each(messages)
        finally:
            current_principal.reset(reset)

    def _dispatch_each(self, messages) -> list:
        responses = []
        for message in messages:
            if not isinstance(message, dict):
                responses.append(error_response(None, INVALID_REQUEST, "Invalid Request"))
                continue
            if "method" not in message and ("result" in message or "error" in message):
                continue  # a client's reply to a server request — nothing to answer
            if "method" in message and not isinstance(message["method"], str):
                responses.append(error_response(
                    message.get("id"), INVALID_REQUEST, "Invalid Request: 'method' must be a string"
                ))
                continue
            response = self.server.mcp.dispatch(message)
            if response is not None:
                responses.append(response)
        return responses

    def _stream(self, session: Session, first_event: bytes | None = None,
                still_valid=lambda: True) -> None:
        """Hold an SSE stream open, relaying the session outbox and pinging."""
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        for key, value in {**self._security_headers(), **self._cors_headers()}.items():
            self.send_header(key, value)
        if session.kind == "streamable":
            self.send_header(SESSION_HEADER, session.id)
        self.end_headers()
        try:
            if first_event:
                self.wfile.write(first_event)
            self.wfile.write(b": stream open\n\n")
            self.wfile.flush()
            while not self.server.stopping.is_set():
                try:
                    item = session.outbox.get(timeout=self.server.config.ping_interval)
                except queue.Empty:
                    if not still_valid():
                        break  # token expired or revoked mid-stream
                    session.touch()  # an attached stream keeps the session alive
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if item is _STREAM_CLOSED:
                    break
                session.touch()
                self.wfile.write(sse_event(item))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # client went away

    # --- verbs ----------------------------------------------------------------------

    def _handle(self, route) -> None:
        # Keep-alive is allowed again only once the framing gate has passed and
        # the declared body is fully read (at once when it is empty, else in
        # _read_body). Any other reply closes the connection, so unread bytes
        # are never parsed as the next request.
        self._client_wants_close = self.close_connection
        self.close_connection = True
        self._body_length = 0
        ip_slot = None
        try:
            ip_slot = self._ip_slot()
            self._body_length = self._check_framing()
            if self._body_length == 0:
                self.close_connection = self._client_wants_close
            self._check_origin()
            route()
        except _Reject as exc:
            self._send_reject(exc)
        except OAuthError as exc:
            self._send_json(exc.status, exc.body(), {**_NO_STORE, **exc.headers})
        except Exception as exc:  # noqa: BLE001 - answer, don't drop the connection
            log(f"Internal error handling {self.command} {_redact(self._path())}: "
                f"{type(exc).__name__}")
            self.close_connection = True
            try:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                error_response(None, INTERNAL_ERROR, "Internal error"))
            except OSError:
                pass
        finally:
            if ip_slot is not None:
                self.server.per_ip.release(ip_slot)

    def _client_ip(self) -> str:
        return client_ip(self.headers, self.client_address[0],
                         self.server.config.trust_forwarded_for)

    def _ip_slot(self) -> str | None:
        """Behind a trusted proxy, hold the caller IP's slot for this request
        (an SSE stream holds it while open), or 429. Without one the accept
        loop already counted the TCP peer: None."""
        if not self.server.config.trust_forwarded_for:
            return None
        ip = self._client_ip()
        if not self.server.per_ip.acquire(ip):
            raise _Reject(HTTPStatus.TOO_MANY_REQUESTS,
                          "Too many connections from this address; try again later",
                          SERVER_BUSY, {"Retry-After": "1"})
        return ip

    def _route(self, routes: dict) -> None:
        path = self._path()
        # A keep-alive connection reuses this handler: the key is per request.
        self._url_key = None
        if path.startswith(oauth.URL_KEY_PATH) and self.server.oauth is not None:
            key = path[len(oauth.URL_KEY_PATH):]
            if key and "/" not in key:
                self._url_key = key
                path = "/mcp"  # the Streamable HTTP handlers, authenticated by key
        route = routes.get(path)
        if route is None and self.server.oauth is not None:
            route = self._oauth_routes().get((self.command, path))
        self._handle(route or self._not_found)

    def _oauth_routes(self) -> dict:
        routes = {
            ("GET", "/.well-known/oauth-protected-resource"): self._get_resource_metadata,
            ("GET", "/.well-known/oauth-authorization-server"): self._get_as_metadata,
            ("POST", "/token"): self._post_token,
        }
        if self.server.oauth.config.login_mode != "none":
            routes.update({
                ("POST", "/register"): self._post_register,
                ("GET", "/authorize"): self._get_authorize,
                ("POST", "/authorize"): self._post_authorize,
                ("GET", "/oauth/callback"): self._get_callback,
                ("POST", "/oauth/dev-login"): self._post_dev_login,
            })
        return routes

    def do_OPTIONS(self):  # noqa: N802
        def route():
            self._send(HTTPStatus.NO_CONTENT, headers={
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                # Mcp-Protocol-Version / Last-Event-ID are sent by newer MCP clients.
                "Access-Control-Allow-Headers": (
                    "Content-Type, Mcp-Session-Id, Authorization, "
                    "Mcp-Protocol-Version, Last-Event-ID"
                ),
                "Access-Control-Max-Age": str(CORS_MAX_AGE),
            })
        self._handle(route)

    def do_GET(self):  # noqa: N802
        self._route({"/healthz": self._get_healthz, "/mcp": self._get_mcp, "/sse": self._get_sse,
                     **{path: self._get_brand for path in _BRAND_FILES}})

    def do_POST(self):  # noqa: N802
        self._route({"/mcp": self._post_mcp, "/messages": self._post_messages})

    def do_DELETE(self):  # noqa: N802
        self._route({"/mcp": self._delete_mcp})

    def _not_found(self) -> None:
        raise _Reject(HTTPStatus.NOT_FOUND, "Not found")

    def _get_brand(self) -> None:
        # Unauthenticated: connector UIs fetch the icon before anyone signs in.
        name, content_type = _BRAND_FILES[self._path()]
        self._send(HTTPStatus.OK, brand_asset(name), content_type,
                   {"Cache-Control": "public, max-age=86400"})

    # --- MCP routes ---------------------------------------------------------------------

    def _get_healthz(self) -> None:
        # Unauthenticated: status and counts only, never the catalog itself.
        mcp = self.server.mcp
        try:
            entries = list(mcp._scoped_entries()) if mcp.loader is not None else []
        except SkillsError as exc:
            log(f"healthz: {exc}")
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                            {"status": "error", "transport": "http", "error": "skills hub unavailable"})
            return
        storage = "postgres" if mcp.database is not None else "files"
        if mcp.database is not None and not mcp.database.ping():
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE,
                            {"status": "error", "transport": "http", "storage": storage,
                             "error": "database unavailable"})
            return
        self._send_json(HTTPStatus.OK, {
            "status": "ok",
            "transport": "http",
            "storage": storage,
            "skills": len(entries),
            "themes": len({theme for theme, _ in entries}),
            "tools": len(mcp.handle_tools_list({})["tools"]),
        })

    def _session_or_404(self, session_id: str | None, kind: str, info: TokenInfo | None) -> Session:
        session = self.server.sessions.get(session_id, kind, self._principal(info))
        if session is None:
            raise _Reject(HTTPStatus.NOT_FOUND, "Session not found", SESSION_NOT_FOUND)
        return session

    def _new_session(self, kind: str, info: TokenInfo | None) -> Session:
        try:
            return self.server.sessions.create(kind, self._principal(info))
        except SessionFull:
            raise _Reject(HTTPStatus.SERVICE_UNAVAILABLE,
                          "Too many sessions; try again later", SERVER_BUSY)
        except PrincipalSessionsFull:
            # 429, not the global cap's 503: the server has room, this caller
            # is over its share and must end a session (DELETE /mcp) first.
            raise _Reject(HTTPStatus.TOO_MANY_REQUESTS,
                          "Too many sessions for this client; end one or try again later",
                          SERVER_BUSY, {"Retry-After": str(PER_CALLER_RETRY_AFTER)})

    @contextlib.contextmanager
    def _stream_slot(self, info: TokenInfo | None):
        """Hold one of the principal's SSE stream slots for the block, or 429.
        No cap with auth off (principal ``None``)."""
        principal = limit_key(self._principal(info))
        if principal is None:
            yield
            return
        streams = self.server.principal_streams
        if not streams.acquire(principal):
            self.close_connection = True  # free this connection's slot right away
            raise _Reject(HTTPStatus.TOO_MANY_REQUESTS,
                          "Too many open streams for this client; close one or try again later",
                          SERVER_BUSY, {"Retry-After": str(PER_CALLER_RETRY_AFTER)})
        try:
            yield
        finally:
            streams.release(principal)

    def _post_mcp(self) -> None:
        info = self._authenticate()
        messages, is_batch = self._parse_jsonrpc(self._read_body())
        self._check_scopes(info, messages)
        is_init = any(isinstance(m, dict) and m.get("method") == "initialize"
                      for m in messages)
        if is_init and is_batch:
            raise _Reject(HTTPStatus.BAD_REQUEST,
                          "Invalid Request: initialize must not be batched")

        if is_init:
            session = self._new_session("streamable", info)
        else:
            session = self._session_or_404(self.headers.get(SESSION_HEADER), "streamable", info)

        try:
            responses = self._dispatch_all(messages, info)
        except BaseException:
            if is_init:
                self.server.sessions.terminate(session.id)
            raise
        headers = {SESSION_HEADER: session.id}
        if is_init and (not responses or "error" in responses[0]):
            self.server.sessions.terminate(session.id)
            headers = {}
        if not responses:
            self._send(HTTPStatus.ACCEPTED, headers=headers)
            return
        if prefers_sse(self.headers.get("Accept")):
            body = b"".join(sse_event(r) for r in responses)
            self.close_connection = True
            self._send(HTTPStatus.OK, body, "text/event-stream",
                       {**headers, "Cache-Control": "no-cache"})
            return
        self._send_json(HTTPStatus.OK, responses if is_batch else responses[0], headers)

    def _get_mcp(self) -> None:
        info = self._authenticate()
        if not accepts_sse(self.headers.get("Accept")):
            raise _Reject(HTTPStatus.METHOD_NOT_ALLOWED,
                          "GET /mcp requires Accept: text/event-stream",
                          headers={"Allow": "POST, DELETE, OPTIONS"})
        session = self._session_or_404(self.headers.get(SESSION_HEADER), "streamable", info)
        if not session.stream_lock.acquire(blocking=False):
            self.close_connection = True  # free this connection's slot right away
            raise _Reject(HTTPStatus.CONFLICT, "A stream is already open for this session")
        try:
            with self._stream_slot(info):
                self._stream(session, still_valid=self._still_valid(info))
        finally:
            session.stream_lock.release()

    def _delete_mcp(self) -> None:
        info = self._authenticate()
        session = self._session_or_404(self.headers.get(SESSION_HEADER), "streamable", info)
        self.server.sessions.terminate(session.id)
        self._send(HTTPStatus.NO_CONTENT)

    def _get_sse(self) -> None:
        info = self._authenticate()
        with self._stream_slot(info):
            session = self._new_session("legacy", info)
            endpoint = f"/messages?sessionId={session.id}"
            try:
                self._stream(session, first_event=sse_event(endpoint, event="endpoint"),
                             still_valid=self._still_valid(info))
            finally:
                # A legacy session lives exactly as long as its stream.
                self.server.sessions.terminate(session.id)

    def _post_messages(self) -> None:
        info = self._authenticate()
        session = self._session_or_404(self._query().get("sessionId"), "legacy", info)
        messages, _ = self._parse_jsonrpc(self._read_body())
        self._check_scopes(info, messages)
        for response in self._dispatch_all(messages, info):
            session.send(response)
        self._send(HTTPStatus.ACCEPTED)

    # --- OAuth routes (unauthenticated by design) -----------------------------------------

    def _get_resource_metadata(self) -> None:
        self._send_json(HTTPStatus.OK, OAuthProvider.protected_resource_metadata(self._origin()))

    def _get_as_metadata(self) -> None:
        self._send_json(HTTPStatus.OK, OAuthProvider.authorization_server_metadata(
            self._origin(), self.server.oauth.config.login_mode))

    def _post_register(self) -> None:
        body = self._read_body()
        try:
            metadata = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError):  # bad JSON or UTF-8, or nested too deep
            raise OAuthError("invalid_client_metadata", "Body must be JSON")
        # Keyed on the caller, not the TCP peer: behind Railway's proxy every
        # peer is the proxy, and one shared bucket would let anyone lock out all.
        response = self.server.oauth.register_client(metadata, self._client_ip())
        self._send_json(HTTPStatus.CREATED, response, _NO_STORE)

    def _login_cookie(self) -> str | None:
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie") or "")
        except http.cookies.CookieError:
            return None
        morsel = cookie.get(oauth.LOGIN_COOKIE)
        return morsel.value if morsel else None

    def _set_login_cookie(self, value: str, max_age: int) -> dict:
        secure = "; Secure" if self._origin().startswith("https://") else ""
        return {"Set-Cookie": (f"{oauth.LOGIN_COOKIE}={value}; Path=/; HttpOnly; "
                               f"SameSite=Lax; Max-Age={max_age}{secure}")}

    def _get_authorize(self) -> None:
        provider = self.server.oauth
        try:
            login = provider.start_authorization(self._query(), self._origin(),
                                                 self._client_ip())
        except AuthorizeError as exc:
            self._send_html(HTTPStatus.BAD_REQUEST, oauth.error_page(str(exc)))
            return
        except OAuthError as exc:
            self._redirect(oauth.add_query(exc.redirect_uri, error=exc.error,
                                           error_description=exc.description,
                                           state=exc.state, iss=self._origin()))
            return
        self._send_html(HTTPStatus.OK, oauth.consent_page(login, provider.config.login_mode),
                        self._set_login_cookie(login["id"], oauth.LOGIN_TTL))

    def _pending_login(self, login_id: str | None) -> dict:
        login = self.server.oauth.get_login(login_id, self._login_cookie())
        if login is None:
            raise _LoginExpired()
        return login

    def _post_authorize(self) -> None:
        provider = self.server.oauth
        form = self._read_form()
        try:
            login = self._pending_login(form.get("login"))
        except _LoginExpired:
            self._send_html(HTTPStatus.BAD_REQUEST, oauth.error_page(
                "This sign-in request expired or was opened in another browser. "
                "Start again from your MCP client."))
            return
        if form.get("action") != "approve":
            provider.pop_login(login["id"])
            self._redirect(provider.error_redirect(login, "access_denied", "The user denied access"))
            return
        if provider.config.login_mode == "github":
            self._redirect(provider.github_authorize_url(login))
        else:
            self._send_html(HTTPStatus.OK, oauth.dev_login_page(login))

    def _finish(self, login: dict, location: str) -> None:
        self.server.oauth.pop_login(login["id"])
        self._redirect(location, self._set_login_cookie("", 0))

    def _get_callback(self) -> None:
        provider = self.server.oauth
        if provider.config.login_mode != "github":
            raise _Reject(HTTPStatus.NOT_FOUND, "Not found")
        query = self._query()
        try:
            login = self._pending_login(query.get("state"))
        except _LoginExpired:
            self._send_html(HTTPStatus.BAD_REQUEST, oauth.error_page(
                "This sign-in request expired or was opened in another browser. "
                "Start again from your MCP client."))
            return
        if query.get("error") or not query.get("code"):
            self._finish(login, provider.error_redirect(login, "access_denied",
                                                        "GitHub sign-in was not completed"))
            return
        try:
            user, github_id = provider.github_user(query["code"], login)
        except OAuthError as exc:
            log(f"OAuth: {exc.description}")
            self._finish(login, provider.error_redirect(login, exc.error, exc.description))
            return
        self._finish(login, provider.complete_authorization(login, user, "github", github_id))

    def _post_dev_login(self) -> None:
        provider = self.server.oauth
        if provider.config.login_mode != "dev":
            raise _Reject(HTTPStatus.NOT_FOUND, "Not found")
        form = self._read_form()
        try:
            login = self._pending_login(form.get("login"))
        except _LoginExpired:
            self._send_html(HTTPStatus.BAD_REQUEST, oauth.error_page(
                "This sign-in request expired. Start again from your MCP client."))
            return
        user = form.get("username", "")
        if not provider.valid_dev_login(user):
            self._send_html(HTTPStatus.BAD_REQUEST, oauth.error_page("Invalid username."))
            return
        self._finish(login, provider.complete_authorization(login, user, "dev"))

    def _post_token(self) -> None:
        form = self._read_form()
        response = self.server.oauth.token(form, self.headers.get("Authorization"),
                                           self._origin())
        self._send_json(HTTPStatus.OK, response, _NO_STORE)


class _LoginExpired(Exception):
    pass


# --- Entry point ------------------------------------------------------------------------


def make_server(mcp: SkillsMCPServer, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                config: HttpConfig | None = None, oauth_provider: OAuthProvider | None = None,
                *, no_auth: bool = False) -> McpHTTPServer:
    """Bind (but don't start) an MCP HTTP server.

    Gated by default: pass an :class:`OAuthProvider`, or ``no_auth=True`` to
    serve without authentication (loopback development only).
    """
    if no_auth:
        if not is_loopback_host(host):
            raise ValueError("no_auth is only allowed on a loopback address")
        oauth_provider = None
    elif oauth_provider is None:
        raise ValueError("the gated HTTP transport needs an OAuthProvider (or no_auth=True)")
    config = config or HttpConfig.from_env()
    if config.max_sessions >= config.max_connections:
        log(f"Warning: NUVEL_MCP_MAX_SESSIONS ({config.max_sessions}) >= "
            f"NUVEL_MCP_MAX_CONNECTIONS ({config.max_connections}): every open SSE stream "
            "holds a connection, so streams can use up all connections before the "
            "session cap applies. Keep sessions below connections.")
    return McpHTTPServer((host, port), mcp, config, oauth_provider)


def serve_http(mcp: SkillsMCPServer, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
               config: HttpConfig | None = None, oauth_provider: OAuthProvider | None = None,
               *, no_auth: bool = False) -> None:
    """Serve until SIGINT/SIGTERM, then close sessions and the socket cleanly."""
    httpd = make_server(mcp, host, port, config, oauth_provider, no_auth=no_auth)

    def _on_signal(signum, _frame):
        log(f"nuvel-skills MCP HTTP server: {signal.Signals(signum).name}, shutting down.")
        # shutdown() blocks until serve_forever returns, so it can't run on this thread.
        threading.Thread(target=httpd.stop, daemon=True).start()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    where = mcp.loader.skills_dir if mcp.loader is not None else "none (feedback-only)"
    log(f"nuvel-skills MCP server starting (skills_dir={where})")
    log(f"Listening on {httpd.url}  (streamable: /mcp, legacy SSE: /sse, health: /healthz)")
    if httpd.oauth is not None:
        log(f"OAuth 2.1: every MCP method needs a bearer token or a URL key on "
            f"/mcp/k/<key> (login: {httpd.oauth.config.login_mode}, "
            f"store: {httpd.oauth.store.path}).")
    try:
        httpd.serve_forever()
    finally:
        httpd.sessions.close_all()
        httpd.server_close()
        log("nuvel-skills MCP HTTP server stopped.")
