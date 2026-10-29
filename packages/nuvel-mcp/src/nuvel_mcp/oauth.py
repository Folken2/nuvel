"""OAuth 2.1 for the Nuvel Skills MCP server. Stdlib only.

The HTTP transport is both the protected resource and its own authorization
server. Every MCP method needs a bearer access token; this module holds
everything that isn't HTTP plumbing:

    RFC 9728  protected resource metadata    GET  /.well-known/oauth-protected-resource
    RFC 8414  authorization server metadata  GET  /.well-known/oauth-authorization-server
    RFC 7591  dynamic client registration    POST /register (rate-limited)
    OAuth 2.1 authorization code + PKCE      GET  /authorize -> consent -> GitHub login
              (S256 only)                    GET  /oauth/callback
              token endpoint                 POST /token  (authorization_code,
                                                           refresh_token, client_credentials)
    RFC 8707  resource indicators            tokens are bound to this server's origin

Operator-issued URL keys (``nuvel-mcp client create --url-key``) are the one
credential carried in the URL (``/mcp/k/<key>``), for MCP clients that can only
be given a URL. Each key maps to its own client, so the tenant is that client's
``client_id``. ``NUVEL_OAUTH_LOGIN=none`` turns off the interactive login (no
/authorize, no registration), leaving URL keys and client_credentials.

Scopes: ``skills:read`` (every MCP method and the read-only tools) and
``skills:write`` (the tools that write: ``install_skill``,
``propose_improvement``, ``record_feedback``, and any unknown tool, so it fails
closed). A token without a scope is still *authenticated*; missing a scope is
the one ``403`` case.

State lives in one JSON file (``$NUVEL_MCP_OAUTH_STORE``, default
``~/.nuvel/oauth.json``, mode 0600, atomic writes). Tokens, codes and client
secrets are stored only as SHA-256 hashes and are never logged.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

try:  # POSIX: serialize read-modify-write across processes (server + CLI).
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

SCOPE_READ = "skills:read"
SCOPE_WRITE = "skills:write"
SCOPES = (SCOPE_READ, SCOPE_WRITE)

# Tools that only read the hub. Everything else (the write tools, and any tool
# this table doesn't know) needs skills:write.
READ_TOOLS = frozenset({
    "search_skills", "list_skills", "list_themes", "get_skill",
    "fence_value", "check_skill_health",
})
WRITE_TOOLS = frozenset({"install_skill", "propose_improvement", "record_feedback"})

ACCESS_TOKEN_TTL = 3600
REFRESH_TOKEN_TTL = 30 * 24 * 3600
CODE_TTL = 60
LOGIN_TTL = 600  # a pending /authorize waiting for the user to log in
MAX_PENDING_LOGINS = 1000

DEFAULT_STORE = "~/.nuvel/oauth.json"
LOGIN_COOKIE = "nuvel_oauth_login"

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_TIMEOUT = 10

# JSON-RPC error codes for auth failures (implementation-defined range).
UNAUTHORIZED = -32002
FORBIDDEN = -32004

_PKCE_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
_PKCE_CHALLENGE = re.compile(r"[A-Za-z0-9\-_]{43}")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_TABLES = ("clients", "codes", "access_tokens", "refresh_tokens", "url_keys")

URL_KEY_PREFIX = "nvuk_"
URL_KEY_PATH = "/mcp/k/"
# A URL key as it appears in a pasted connector URL: the segment after /k/.
_URL_KEY_IN_URL = re.compile(r"/k/([^/?#\s]+)")

# Accepted values of NUVEL_OAUTH_LOGIN ("" = choose from the credentials set).
LOGIN_SETTINGS = ("", "none")


class OAuthError(Exception):
    """An RFC 6749-style error: ``{"error": ..., "error_description": ...}``."""

    def __init__(self, error: str, description: str, status: int = 400,
                 headers: dict | None = None):
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status
        self.headers = headers or {}

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description}


class AuthorizeError(Exception):
    """An /authorize failure that must NOT redirect (bad client or redirect URI)."""


class OAuthConfigError(Exception):
    """The server can't run gated with this configuration."""


class OAuthStoreError(Exception):
    """The OAuth store file is unreadable; fail closed."""


# --- Small helpers ---------------------------------------------------------------


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _find(table: dict, digest: str):
    """Constant-time lookup: compare the digest against every key, no early exit."""
    match = None
    probe = digest.encode("ascii")
    for key, record in table.items():
        if hmac.compare_digest(str(key).encode("utf-8", "replace"), probe):
            match = (key, record)
    return match


def _split_env(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def normalize_public_url(url: str) -> str:
    """``https://host[:port][/]`` -> ``https://host[:port]``; the server lives at the root."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"NUVEL_MCP_PUBLIC_URL must be an http(s) URL, got {url!r}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("NUVEL_MCP_PUBLIC_URL must be an origin with no path, "
                         f"query or fragment, got {url!r}")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def public_url_from_env() -> str | None:
    raw = os.environ.get("NUVEL_MCP_PUBLIC_URL", "").strip()
    return normalize_public_url(raw) if raw else None


def resource_matches(value: str, origin: str) -> bool:
    """True when an RFC 8707 ``resource`` names this server.

    The canonical resource is the origin (that's what the root
    ``/.well-known/oauth-protected-resource`` advertises), but MCP clients may
    send the endpoint URL they connect to, so ``/mcp`` and ``/sse`` match too.
    """
    parts = urllib.parse.urlsplit(value.strip())
    if parts.query or parts.fragment or not parts.scheme or not parts.netloc:
        return False
    if parts.path.rstrip("/") not in ("", "/mcp", "/sse"):
        return False
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}" == origin


def parse_scope(raw: str | None) -> frozenset[str]:
    """Space-separated scopes -> set; empty/absent means every supported scope."""
    if raw is None or not raw.strip():
        return frozenset(SCOPES)
    scopes = frozenset(raw.split())
    if not scopes <= frozenset(SCOPES):
        raise OAuthError("invalid_scope", f"Supported scopes: {' '.join(SCOPES)}")
    return scopes


def format_scope(scopes) -> str:
    return " ".join(s for s in SCOPES if s in scopes)


def required_scope(message) -> str:
    """The scope a JSON-RPC message needs: write tools need skills:write."""
    if isinstance(message, dict) and message.get("method") == "tools/call":
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        return SCOPE_READ if name in READ_TOOLS else SCOPE_WRITE
    return SCOPE_READ


def _is_dcr_loopback(uri: str) -> bool:
    parts = urllib.parse.urlsplit(uri)
    try:
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        return False
    return (parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")
            and not parts.fragment and not parts.username and not parts.password)


def _valid_absolute_uri(uri: str) -> bool:
    parts = urllib.parse.urlsplit(uri)
    if parts.fragment or not parts.netloc or parts.username or parts.password:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


def _redirect_matches(registered: list[str], presented: str) -> bool:
    """Exact match, except loopback redirect URIs may use any port (RFC 8252 §7.3)."""
    if presented in registered:
        return True
    p = urllib.parse.urlsplit(presented)
    if p.scheme != "http" or p.hostname not in ("localhost", "127.0.0.1"):
        return False
    for uri in registered:
        r = urllib.parse.urlsplit(uri)
        if (r.scheme, r.hostname, r.path, r.query) == (p.scheme, p.hostname, p.path, p.query):
            return True
    return False


def add_query(uri: str, **params) -> str:
    parts = urllib.parse.urlsplit(uri)
    extra = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urllib.parse.urlunsplit(parts._replace(query=query))


# --- Config ----------------------------------------------------------------------


@dataclass
class OAuthConfig:
    store_path: Path = field(default_factory=lambda: Path(DEFAULT_STORE).expanduser())
    github_client_id: str | None = None
    github_client_secret: str | None = None
    dev_login: bool = False
    # NUVEL_OAUTH_LOGIN: "none" turns the interactive login off; "" picks it
    # from the credentials. Other values are refused at startup by the CLI.
    login: str = ""
    # None = any authenticated user may log in (warned at startup).
    allowed_users: frozenset[str] | None = None
    redirect_allowlist: frozenset[str] = frozenset()
    register_limit: int = 20  # per client IP per window
    register_global_limit: int = 200  # all IPs per window
    register_window: float = 3600.0
    github_authorize_url: str = GITHUB_AUTHORIZE_URL
    github_token_url: str = GITHUB_TOKEN_URL
    github_user_url: str = GITHUB_USER_URL

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        store = os.environ.get("NUVEL_MCP_OAUTH_STORE", "").strip() or DEFAULT_STORE
        users = _split_env("NUVEL_OAUTH_ALLOWED_USERS")
        return cls(
            store_path=Path(store).expanduser(),
            github_client_id=os.environ.get("NUVEL_OAUTH_GITHUB_CLIENT_ID", "").strip() or None,
            github_client_secret=(
                os.environ.get("NUVEL_OAUTH_GITHUB_CLIENT_SECRET", "").strip() or None),
            dev_login=os.environ.get("NUVEL_OAUTH_DEV_LOGIN", "").strip() == "1",
            login=os.environ.get("NUVEL_OAUTH_LOGIN", "").strip().lower(),
            allowed_users=frozenset(u.lower() for u in users) if users else None,
            redirect_allowlist=frozenset(_split_env("NUVEL_OAUTH_REDIRECT_ALLOWLIST")),
        )

    @property
    def login_mode(self) -> str | None:
        if self.login == "none":
            return "none"
        if self.github_client_id and self.github_client_secret:
            return "github"
        if self.dev_login:
            return "dev"
        return None


# --- Store -----------------------------------------------------------------------


class OAuthStore:
    """One JSON file holding clients, codes and tokens (hashes only).

    Reads are cached by (inode, mtime, size), so a CLI write (``nuvel mcp
    client create``, ``token revoke``) is picked up by a running server on its
    next request. Writes are serialized with a thread lock plus an ``flock`` on
    ``<store>.lock`` and land atomically via a temp file + ``os.replace``.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._cache: dict | None = None
        self._stamp = None

    def _stat(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _load(self) -> dict:
        stamp = self._stat()
        if self._cache is not None and stamp == self._stamp:
            return self._cache
        if stamp is None:
            data: dict = {"version": 1}
        else:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise OAuthStoreError(f"cannot read OAuth store {self.path}: {exc}") from exc
            if not isinstance(data, dict):
                raise OAuthStoreError(f"OAuth store {self.path} is not a JSON object")
        for table in _TABLES:
            if not isinstance(data.get(table), dict):
                data[table] = {}
        self._cache, self._stamp = data, stamp
        return data

    @contextlib.contextmanager
    def reading(self):
        """Yield the current data (read-only) while holding the lock."""
        with self._lock:
            yield self._load()

    @contextlib.contextmanager
    def transaction(self):
        """Yield a mutable copy; it is written back only if the block succeeds."""
        with self._lock, self._file_lock():
            data = copy.deepcopy(self._load())
            yield data
            self._save(data)

    def _ensure_dir(self) -> None:
        missing = []
        parent = self.path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                continue
            os.chmod(directory, 0o700)

    @contextlib.contextmanager
    def _file_lock(self):
        if fcntl is None:  # pragma: no cover - Windows
            yield
            return
        self._ensure_dir()
        fd = os.open(f"{self.path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _save(self, data: dict) -> None:
        self._ensure_dir()
        fd, tmp = tempfile.mkstemp(prefix=".oauth-", suffix=".tmp", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        self._cache, self._stamp = data, self._stat()


# --- Rate limiting ---------------------------------------------------------------


class RateLimiter:
    """Sliding-window limit per key plus a global cap."""

    def __init__(self, per_key: int, total: int, window: float, clock):
        self.per_key, self.total, self.window, self.clock = per_key, total, window, clock
        self._hits: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def hit(self, key: str) -> float:
        """Record a hit; return 0 if allowed, else seconds until a slot frees."""
        with self._lock:
            now = self.clock()
            cutoff = now - self.window
            while self._all and self._all[0] <= cutoff:
                self._all.popleft()
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            for bucket, limit in ((hits, self.per_key), (self._all, self.total)):
                if len(bucket) >= limit:
                    return max(1.0, bucket[0] + self.window - now)
            hits.append(now)
            self._all.append(now)
            for stale in [k for k, v in self._hits.items() if not v]:
                del self._hits[stale]
            return 0.0


# --- Provider --------------------------------------------------------------------


@dataclass(frozen=True)
class TokenInfo:
    client_id: str
    subject: str
    scope: frozenset
    resource: str
    expires_at: float

    @property
    def principal(self) -> tuple[str, str]:
        return (self.client_id, self.subject)


class OAuthProvider:
    """Authorization server + token validation over an :class:`OAuthStore`."""

    def __init__(self, config: OAuthConfig, store: OAuthStore | None = None, clock=time.time):
        self.config = config
        self.store = store or OAuthStore(config.store_path)
        self.clock = clock
        self._logins: dict[str, dict] = {}
        self._logins_lock = threading.Lock()
        self.register_limiter = RateLimiter(config.register_limit, config.register_global_limit,
                                            config.register_window, lambda: self.clock())

    # --- metadata ----------------------------------------------------------------

    @staticmethod
    def protected_resource_metadata(origin: str) -> dict:
        return {
            "resource": origin,
            "authorization_servers": [origin],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Nuvel Skills MCP",
        }

    @staticmethod
    def authorization_server_metadata(origin: str, login_mode: str | None = None) -> dict:
        if login_mode == "none":  # no interactive login: operator-issued clients only
            return {
                "issuer": origin,
                "token_endpoint": f"{origin}/token",
                "scopes_supported": list(SCOPES),
                "grant_types_supported": ["client_credentials"],
                "token_endpoint_auth_methods_supported": [
                    "client_secret_basic", "client_secret_post"],
            }
        return {
            "issuer": origin,
            "authorization_endpoint": f"{origin}/authorize",
            "token_endpoint": f"{origin}/token",
            "registration_endpoint": f"{origin}/register",
            "scopes_supported": list(SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": [
                "none", "client_secret_basic", "client_secret_post"],
            "authorization_response_iss_parameter_supported": True,
        }

    # --- clients -------------------------------------------------------------------

    def _new_client(self, data: dict, *, name: str, redirect_uris: list[str], auth_method: str,
                    grant_types: list[str], source: str) -> tuple[dict, str | None]:
        client_id = "nvcl_" + secrets.token_urlsafe(16)
        secret = None
        record = {
            "client_id": client_id,
            "client_name": name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": auth_method,
            "grant_types": grant_types,
            "response_types": ["code"] if "authorization_code" in grant_types else [],
            "client_id_issued_at": int(self.clock()),
            "source": source,
        }
        if auth_method != "none":
            secret = "nvcs_" + secrets.token_urlsafe(32)
            record["client_secret_hash"] = token_hash(secret)
        data["clients"][client_id] = record
        return record, secret

    def register_client(self, metadata, remote_addr: str) -> dict:
        """RFC 7591 dynamic registration. Never grants client_credentials."""
        retry = self.register_limiter.hit(remote_addr)
        if retry:
            raise OAuthError("invalid_request", "Too many registrations; retry later", 429,
                             {"Retry-After": str(int(retry) + 1)})
        if not isinstance(metadata, dict):
            raise OAuthError("invalid_client_metadata", "Body must be a JSON object")
        uris = metadata.get("redirect_uris")
        if (not isinstance(uris, list) or not uris
                or not all(isinstance(u, str) and u for u in uris)):
            raise OAuthError("invalid_redirect_uri", "redirect_uris must be a non-empty list")
        for uri in uris:
            if not (_is_dcr_loopback(uri) or uri in self.config.redirect_allowlist):
                raise OAuthError(
                    "invalid_redirect_uri",
                    "Redirect URIs must be http://localhost or http://127.0.0.1 (any port), "
                    "or listed in NUVEL_OAUTH_REDIRECT_ALLOWLIST")
        method = metadata.get("token_endpoint_auth_method", "client_secret_basic")
        if method not in ("none", "client_secret_basic", "client_secret_post"):
            raise OAuthError("invalid_client_metadata",
                             f"Unsupported token_endpoint_auth_method: {method!r}")
        grants = metadata.get("grant_types", ["authorization_code", "refresh_token"])
        if (not isinstance(grants, list) or "authorization_code" not in grants
                or not set(grants) <= {"authorization_code", "refresh_token"}):
            raise OAuthError(
                "invalid_client_metadata",
                "grant_types must include authorization_code and may add refresh_token; "
                "client_credentials is only for operator-issued clients "
                "(nuvel mcp client create)")
        response_types = metadata.get("response_types", ["code"])
        if response_types != ["code"]:
            raise OAuthError("invalid_client_metadata", "response_types must be [\"code\"]")
        name = metadata.get("client_name", "")
        if not isinstance(name, str) or len(name) > 200:
            raise OAuthError("invalid_client_metadata", "client_name must be a short string")
        with self.store.transaction() as data:
            record, secret = self._new_client(
                data, name=name, redirect_uris=list(uris), auth_method=method,
                grant_types=list(grants), source="dcr")
        response = {k: record[k] for k in (
            "client_id", "client_name", "redirect_uris", "token_endpoint_auth_method",
            "grant_types", "response_types", "client_id_issued_at")}
        if secret:
            response["client_secret"] = secret
            response["client_secret_expires_at"] = 0
        return response

    def create_client(self, name: str, redirect_uris: list[str] | None = None, *,
                      url_key: bool = False) -> tuple[dict, str]:
        """Operator-issued client (``nuvel mcp client create``).

        Returns ``(record, credential)``: a confidential client and its secret,
        or with ``url_key`` a client that has no secret and no grant types (it
        only names the tenant) and its URL key. Either is shown once; only its
        hash is stored.
        """
        redirect_uris = list(redirect_uris or [])
        if url_key:
            if redirect_uris:
                raise ValueError("a URL-key client takes no redirect URIs")
            key = URL_KEY_PREFIX + secrets.token_urlsafe(32)
            with self.store.transaction() as data:
                record, _ = self._new_client(
                    data, name=name, redirect_uris=[], auth_method="none",
                    grant_types=[], source="cli")
                record["url_key"] = True
                data["url_keys"][token_hash(key)] = {
                    "client_id": record["client_id"], "issued_at": int(self.clock()),
                    "revoked": False}
            return record, key
        for uri in redirect_uris:
            if not _valid_absolute_uri(uri):
                raise ValueError(f"redirect URI must be https://, or http:// on loopback: {uri!r}")
        grants = ["client_credentials"]
        if redirect_uris:
            grants += ["authorization_code", "refresh_token"]
        with self.store.transaction() as data:
            record, secret = self._new_client(
                data, name=name, redirect_uris=redirect_uris,
                auth_method="client_secret_basic", grant_types=grants, source="cli")
        return record, secret

    def list_clients(self) -> list[dict]:
        now = self.clock()
        with self.store.reading() as data:
            active: dict[str, int] = {}
            for rec in data["access_tokens"].values():
                if not rec.get("revoked") and rec.get("expires_at", 0) > now:
                    active[rec.get("client_id")] = active.get(rec.get("client_id"), 0) + 1
            clients = []
            for rec in data["clients"].values():
                view = {k: v for k, v in rec.items() if k != "client_secret_hash"}
                view["confidential"] = "client_secret_hash" in rec
                view["active_access_tokens"] = active.get(rec["client_id"], 0)
                clients.append(view)
        return sorted(clients, key=lambda c: c.get("client_id_issued_at", 0))

    def delete_client(self, client_id: str) -> bool:
        with self.store.transaction() as data:
            if data["clients"].pop(client_id, None) is None:
                return False
            self._revoke_where(data, lambda rec: rec.get("client_id") == client_id)
            for key in [k for k, r in data["url_keys"].items()
                        if r.get("client_id") == client_id]:
                del data["url_keys"][key]
        return True

    # --- revocation ------------------------------------------------------------------

    @staticmethod
    def _revoke_where(data: dict, predicate) -> dict:
        """Revoke matching tokens and URL keys; ``url_keys`` only counts for a
        client-wide predicate (grant ids never match a key record)."""
        counts = {"access_tokens": 0, "refresh_tokens": 0, "url_keys": 0}
        for table in counts:
            for rec in data[table].values():
                if not rec.get("revoked") and predicate(rec):
                    rec["revoked"] = True
                    counts[table] += 1
        return counts

    def revoke(self, value: str) -> dict | None:
        """Revoke a token (access or refresh), a URL key (or a connector URL
        carrying one), or every token and key of a client id."""
        value = value.strip()
        in_url = _URL_KEY_IN_URL.search(value)
        if in_url:
            value = in_url.group(1)
        with self.store.transaction() as data:
            if value in data["clients"]:
                counts = self._revoke_where(data, lambda r: r.get("client_id") == value)
                return {"kind": "client", "client_id": value, **counts}
            digest = token_hash(value)
            found = _find(data["url_keys"], digest)
            if found is not None:
                rec = found[1]
                counts = {"access_tokens": 0, "refresh_tokens": 0,
                          "url_keys": 0 if rec.get("revoked") else 1}
                rec["revoked"] = True
                return {"kind": "url_key", "client_id": rec.get("client_id"), **counts}
            for table in ("access_tokens", "refresh_tokens"):
                found = _find(data[table], digest)
                if found is None:
                    continue
                _, rec = found
                if table == "refresh_tokens":
                    grant = rec.get("grant_id")
                    counts = self._revoke_where(data, lambda r: r.get("grant_id") == grant)
                else:
                    counts = {"access_tokens": 0 if rec.get("revoked") else 1,
                              "refresh_tokens": 0, "url_keys": 0}
                    rec["revoked"] = True
                return {"kind": table[:-1], "client_id": rec.get("client_id"), **counts}
        return None

    # --- authorization request ----------------------------------------------------------

    def start_authorization(self, params: dict[str, str], origin: str) -> dict:
        """Validate an /authorize request and park it until the user logs in.

        Raises AuthorizeError (show an error page) for a bad client/redirect URI,
        and OAuthError with ``redirect_uri``/``state`` attributes set for errors
        that go back to the client.
        """
        with self.store.reading() as data:
            client = data["clients"].get(params.get("client_id") or "")
            client = copy.deepcopy(client) if client else None
        if client is None:
            raise AuthorizeError("Unknown client_id.")
        redirect_uri = params.get("redirect_uri") or ""
        if not redirect_uri or not _redirect_matches(client.get("redirect_uris", []), redirect_uri):
            raise AuthorizeError("redirect_uri is missing or not registered for this client.")
        if "authorization_code" not in client.get("grant_types", []):
            raise AuthorizeError("This client is not allowed to use the authorization code flow.")

        state = params.get("state")

        def fail(error, description):
            exc = OAuthError(error, description)
            exc.redirect_uri, exc.state = redirect_uri, state
            return exc

        if params.get("response_type") != "code":
            raise fail("unsupported_response_type", "response_type must be 'code'")
        if not state or len(state) > 1024:
            raise fail("invalid_request", "state is required")
        challenge = params.get("code_challenge") or ""
        if not _PKCE_CHALLENGE.fullmatch(challenge):
            raise fail("invalid_request", "code_challenge (PKCE) is required")
        if params.get("code_challenge_method") != "S256":
            raise fail("invalid_request", "code_challenge_method must be S256")
        resource = params.get("resource")
        if resource is not None and not resource_matches(resource, origin):
            raise fail("invalid_target", "resource does not identify this server")
        try:
            scope = parse_scope(params.get("scope"))
        except OAuthError as exc:
            raise fail(exc.error, exc.description)

        login = {
            "id": secrets.token_urlsafe(32),
            "client_id": client["client_id"],
            "client_name": client.get("client_name") or client["client_id"],
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "scope": sorted(scope),
            "resource": origin,
            "origin": origin,
            "expires_at": self.clock() + LOGIN_TTL,
        }
        with self._logins_lock:
            now = self.clock()
            for key in [k for k, v in self._logins.items() if v["expires_at"] <= now]:
                del self._logins[key]
            if len(self._logins) >= MAX_PENDING_LOGINS:
                raise AuthorizeError("Too many pending sign-ins; try again shortly.")
            self._logins[login["id"]] = login
        return login

    def get_login(self, login_id: str | None, cookie: str | None) -> dict | None:
        """The pending login, if it exists, is unexpired and matches the browser cookie."""
        if not login_id or not cookie or not hmac.compare_digest(
                login_id.encode("utf-8", "replace"), cookie.encode("utf-8", "replace")):
            return None
        with self._logins_lock:
            login = self._logins.get(login_id)
            if login is None or login["expires_at"] <= self.clock():
                return None
            return login

    def pop_login(self, login_id: str) -> dict | None:
        with self._logins_lock:
            return self._logins.pop(login_id, None)

    @staticmethod
    def error_redirect(login: dict, error: str, description: str) -> str:
        return add_query(login["redirect_uri"], error=error, error_description=description,
                         state=login["state"], iss=login["origin"])

    def user_allowed(self, login: str) -> bool:
        allowed = self.config.allowed_users
        return allowed is None or login.lower() in allowed

    def complete_authorization(self, login: dict, user: str, idp: str) -> str:
        """Issue a single-use code for a logged-in user; return the client redirect."""
        if not self.user_allowed(user):
            return self.error_redirect(login, "access_denied", "This user is not allowed")
        code = "nvac_" + secrets.token_urlsafe(32)
        now = self.clock()
        with self.store.transaction() as data:
            self._prune(data, now)
            data["codes"][token_hash(code)] = {
                "client_id": login["client_id"],
                "redirect_uri": login["redirect_uri"],
                "code_challenge": login["code_challenge"],
                "code_challenge_method": "S256",
                "scope": login["scope"],
                "resource": login["resource"],
                "subject": f"{idp}:{user}",
                "grant_id": secrets.token_urlsafe(16),
                "issued_at": now,
                "expires_at": now + CODE_TTL,
                "used": False,
            }
        return add_query(login["redirect_uri"], code=code, state=login["state"],
                         iss=login["origin"])

    # --- GitHub login ---------------------------------------------------------------------

    def github_authorize_url(self, login: dict) -> str:
        return add_query(self.config.github_authorize_url,
                         client_id=self.config.github_client_id,
                         redirect_uri=f"{login['origin']}/oauth/callback",
                         state=login["id"], allow_signup="false")

    def github_user(self, code: str, login: dict) -> str:
        """Exchange GitHub's code for the user's login. Raises OAuthError."""
        form = urllib.parse.urlencode({
            "client_id": self.config.github_client_id,
            "client_secret": self.config.github_client_secret,
            "code": code,
            "redirect_uri": f"{login['origin']}/oauth/callback",
        }).encode("ascii")
        headers = {"Accept": "application/json", "User-Agent": "nuvel-skills-mcp"}
        try:
            req = urllib.request.Request(self.config.github_token_url, data=form,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT) as resp:
                gh_token = json.loads(resp.read().decode("utf-8")).get("access_token")
            if not gh_token:
                raise OAuthError("access_denied", "GitHub did not issue a token")
            req = urllib.request.Request(self.config.github_user_url, headers={
                **headers, "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {gh_token}"})
            with urllib.request.urlopen(req, timeout=GITHUB_TIMEOUT) as resp:
                user = json.loads(resp.read().decode("utf-8")).get("login")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OAuthError("server_error",
                             f"GitHub login failed ({type(exc).__name__})") from None
        if not isinstance(user, str) or not _LOGIN.fullmatch(user):
            raise OAuthError("server_error", "GitHub returned no usable login")
        return user

    @staticmethod
    def valid_dev_login(user: str) -> bool:
        return bool(_LOGIN.fullmatch(user or ""))

    # --- token endpoint -----------------------------------------------------------------------

    def _authenticate_client(self, data: dict, form: dict, authorization: str | None) -> dict:
        client_id = form.get("client_id")
        secret = form.get("client_secret")
        challenge = {"WWW-Authenticate": 'Basic realm="nuvel-skills"'}
        if authorization and authorization[:6].lower() == "basic ":
            if secret is not None:
                raise OAuthError("invalid_request", "Use one client authentication method")
            try:
                raw = base64.b64decode(authorization[6:].strip(), validate=True).decode("utf-8")
                basic_id, sep, basic_secret = raw.partition(":")
                if not sep:
                    raise ValueError
            except ValueError:
                raise OAuthError("invalid_client", "Malformed Basic credentials", 401, challenge)
            basic_id = urllib.parse.unquote_plus(basic_id)
            if client_id is not None and client_id != basic_id:
                raise OAuthError("invalid_client", "Client authentication failed", 401, challenge)
            client_id, secret = basic_id, urllib.parse.unquote_plus(basic_secret)
        if not client_id:
            raise OAuthError("invalid_client", "Client authentication failed", 401, challenge)
        client = data["clients"].get(client_id)
        expected = client.get("client_secret_hash") if client else None
        if client is None:
            ok = False
        elif expected:  # confidential: a matching secret is required
            ok = secret is not None and hmac.compare_digest(
                token_hash(secret).encode("ascii"), expected.encode("ascii"))
        else:  # public: must not present a secret
            ok = secret is None
        if not ok:
            raise OAuthError("invalid_client", "Client authentication failed", 401, challenge)
        return client

    def token(self, form: dict[str, str], authorization: str | None, origin: str) -> dict:
        """Handle POST /token. Returns the JSON response or raises OAuthError."""
        grant_type = form.get("grant_type")
        resource = form.get("resource")
        if resource is not None and not resource_matches(resource, origin):
            raise OAuthError("invalid_target", "resource does not identify this server")
        if grant_type not in ("authorization_code", "refresh_token", "client_credentials"):
            raise OAuthError("unsupported_grant_type", f"Unsupported grant_type: {grant_type!r}")

        now = self.clock()
        outcome: dict | OAuthError
        # Errors are returned out of the transaction rather than raised, so that
        # consuming a code (or revoking a grant on replay) is still persisted.
        with self.store.transaction() as data:
            self._prune(data, now)
            client = self._authenticate_client(data, form, authorization)
            if grant_type not in client.get("grant_types", []):
                outcome = OAuthError("unauthorized_client",
                                     f"This client may not use {grant_type}")
            elif grant_type == "authorization_code":
                outcome = self._grant_code(data, client, form, origin, now)
            elif grant_type == "refresh_token":
                outcome = self._grant_refresh(data, client, form, origin, now)
            else:
                outcome = self._grant_client_credentials(data, client, form, origin, now)
        if isinstance(outcome, OAuthError):
            raise outcome
        return outcome

    def _issue(self, data: dict, *, client_id: str, subject: str, scope, resource: str,
               grant_id: str, refresh: bool, now: float) -> dict:
        access = "nvat_" + secrets.token_urlsafe(32)
        base = {"client_id": client_id, "subject": subject, "scope": sorted(scope),
                "resource": resource, "grant_id": grant_id, "issued_at": now,
                "revoked": False}
        data["access_tokens"][token_hash(access)] = {**base, "expires_at": now + ACCESS_TOKEN_TTL}
        response = {"access_token": access, "token_type": "Bearer",
                    "expires_in": ACCESS_TOKEN_TTL, "scope": format_scope(scope)}
        if refresh:
            refresh_token = "nvrt_" + secrets.token_urlsafe(32)
            data["refresh_tokens"][token_hash(refresh_token)] = {
                **base, "expires_at": now + REFRESH_TOKEN_TTL}
            response["refresh_token"] = refresh_token
        return response

    def _grant_code(self, data, client, form, origin, now):
        code, verifier = form.get("code"), form.get("code_verifier")
        redirect_uri = form.get("redirect_uri")
        if not code or not verifier or not redirect_uri:
            return OAuthError("invalid_request", "code, code_verifier and redirect_uri are required")
        found = _find(data["codes"], token_hash(code))
        if found is None:
            return OAuthError("invalid_grant", "Invalid authorization code")
        _, rec = found
        if rec.get("used"):
            # Replay: revoke whatever the first redemption issued.
            grant = rec.get("grant_id")
            self._revoke_where(data, lambda r: r.get("grant_id") == grant)
            return OAuthError("invalid_grant", "Invalid authorization code")
        rec["used"] = True  # one presentation, successful or not
        if rec["expires_at"] <= now or rec["client_id"] != client["client_id"]:
            return OAuthError("invalid_grant", "Invalid authorization code")
        if redirect_uri != rec["redirect_uri"]:
            return OAuthError("invalid_grant", "redirect_uri does not match")
        if not _PKCE_VERIFIER.fullmatch(verifier) or not hmac.compare_digest(
                pkce_s256(verifier).encode("ascii"), rec["code_challenge"].encode("ascii")):
            return OAuthError("invalid_grant", "PKCE verification failed")
        return self._issue(data, client_id=client["client_id"], subject=rec["subject"],
                           scope=rec["scope"], resource=rec["resource"],
                           grant_id=rec["grant_id"],
                           refresh="refresh_token" in client.get("grant_types", []), now=now)

    def _grant_refresh(self, data, client, form, origin, now):
        presented = form.get("refresh_token")
        if not presented:
            return OAuthError("invalid_request", "refresh_token is required")
        found = _find(data["refresh_tokens"], token_hash(presented))
        if found is None:
            return OAuthError("invalid_grant", "Invalid refresh token")
        _, rec = found
        if rec["client_id"] != client["client_id"]:
            return OAuthError("invalid_grant", "Invalid refresh token")
        if rec.get("revoked"):
            # A rotated-out token came back: assume theft, end the whole grant.
            grant = rec.get("grant_id")
            self._revoke_where(data, lambda r: r.get("grant_id") == grant)
            return OAuthError("invalid_grant", "Invalid refresh token")
        if rec["expires_at"] <= now or rec.get("resource") != origin:
            return OAuthError("invalid_grant", "Invalid refresh token")
        try:
            scope = parse_scope(form.get("scope")) if form.get("scope") else frozenset(rec["scope"])
        except OAuthError as exc:
            return exc
        if not scope <= frozenset(rec["scope"]):
            return OAuthError("invalid_scope", "Cannot widen scope on refresh")
        rec["revoked"] = True  # rotation: the presented token is now spent
        return self._issue(data, client_id=client["client_id"], subject=rec["subject"],
                           scope=scope, resource=rec["resource"], grant_id=rec["grant_id"],
                           refresh=True, now=now)

    def _grant_client_credentials(self, data, client, form, origin, now):
        if "client_secret_hash" not in client:
            return OAuthError("unauthorized_client", "client_credentials needs a confidential client")
        try:
            scope = parse_scope(form.get("scope"))
        except OAuthError as exc:
            return exc
        return self._issue(data, client_id=client["client_id"],
                           subject=f"client:{client['client_id']}", scope=scope,
                           resource=origin, grant_id=secrets.token_urlsafe(16),
                           refresh=False, now=now)

    @staticmethod
    def _prune(data: dict, now: float) -> None:
        for table in ("codes", "access_tokens", "refresh_tokens"):
            for key in [k for k, r in data[table].items() if r.get("expires_at", 0) <= now]:
                del data[table][key]

    # --- validation ---------------------------------------------------------------------------

    def validate(self, token: str | None, resource: str | None) -> TokenInfo | None:
        """The token's grant if it is live, unrevoked, bound to an existing client
        and (when ``resource`` is given) issued for this resource; else None."""
        if not token or len(token) > 512:
            return None
        digest = token_hash(token)
        now = self.clock()
        with self.store.reading() as data:
            found = _find(data["access_tokens"], digest)
            if found is None:
                return None
            rec = found[1]
            if (rec.get("revoked") or rec.get("expires_at", 0) <= now
                    or rec.get("client_id") not in data["clients"]):
                return None
            if resource is not None and rec.get("resource") != resource:
                return None
            return TokenInfo(rec["client_id"], rec.get("subject", ""),
                             frozenset(rec.get("scope", ())), rec.get("resource", ""),
                             rec["expires_at"])

    def validate_url_key(self, key: str | None) -> TokenInfo | None:
        """The key's client as a principal, if the key is unrevoked and its client
        still exists; else None. Same scopes as a client_credentials token."""
        if not key or len(key) > 512:
            return None
        digest = token_hash(key)
        with self.store.reading() as data:
            found = _find(data["url_keys"], digest)
            if found is None:
                return None
            rec = found[1]
            client_id = rec.get("client_id")
            if rec.get("revoked") or client_id not in data["clients"]:
                return None
            return TokenInfo(client_id, f"client:{client_id}", frozenset(SCOPES), "",
                             float("inf"))

    def subject_allowed(self, info: TokenInfo) -> bool:
        """Re-check the user allowlist, so removing a login takes effect immediately."""
        idp, _, user = info.subject.partition(":")
        if idp in ("github", "dev"):
            return self.user_allowed(user)
        return True


# --- Stdio gate ------------------------------------------------------------------------


def stdio_gate(provider: OAuthProvider, token: str | None, resource: str | None):
    """Gate for ``SkillsMCPServer.gate``: re-validates the env token per message."""

    def gate(message):
        msg_id = message.get("id") if isinstance(message, dict) else None
        info = provider.validate(token, resource)
        if info is None:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": UNAUTHORIZED,
                "message": "Unauthorized: set NUVEL_MCP_ACCESS_TOKEN to a valid access token"}}
        scope = required_scope(message)
        if not provider.subject_allowed(info) or scope not in info.scope:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {
                "code": FORBIDDEN, "message": f"Forbidden: requires scope {scope}"}}
        return None

    return gate


# --- HTML -----------------------------------------------------------------------------


_PAGE_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;"
    "color:#1b1b1b}h1{font-size:1.3rem}.box{border:1px solid #ddd;border-radius:8px;"
    "padding:1.2rem}button{font-size:1rem;padding:.5rem 1rem;margin-right:.5rem}"
    ".dev{background:#b00020;color:#fff;padding:.6rem;border-radius:6px;font-weight:bold}"
    "code{background:#f3f3f3;padding:0 .2rem}"
)


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{html.escape(title)}"
            f"</title><style>{_PAGE_CSS}</style></head><body>{body}</body></html>").encode("utf-8")


def consent_page(login: dict, login_mode: str) -> bytes:
    e = html.escape
    host = urllib.parse.urlsplit(login["redirect_uri"]).netloc
    via = "GitHub" if login_mode == "github" else "the DEV login page"
    scopes = "".join(f"<li><code>{e(s)}</code></li>" for s in login["scope"])
    return page("Authorize access", f"""
<div class="box"><h1>Authorize access to Nuvel Skills</h1>
<p><b>{e(login['client_name'])}</b> wants to use this skills server on your behalf.
It will be sent back to <code>{e(host)}</code>.</p>
<p>Requested scopes:</p><ul>{scopes}</ul>
<p>You will sign in with {via} next.</p>
<form method="post" action="/authorize">
<input type="hidden" name="login" value="{e(login['id'])}">
<button type="submit" name="action" value="approve">Continue</button>
<button type="submit" name="action" value="deny">Deny</button>
</form></div>""")


def dev_login_page(login: dict) -> bytes:
    e = html.escape
    return page("DEV LOGIN", f"""
<p class="dev">DEV LOGIN (NUVEL_OAUTH_DEV_LOGIN=1): for local testing only. This page
authenticates nobody; anyone who can reach it can claim any username.</p>
<div class="box"><h1>Sign in (dev)</h1>
<form method="post" action="/oauth/dev-login">
<input type="hidden" name="login" value="{e(login['id'])}">
<label>Username <input name="username" autofocus required pattern="[A-Za-z0-9][A-Za-z0-9-]{{0,38}}"></label>
<button type="submit">Sign in</button>
</form></div>""")


def error_page(message: str) -> bytes:
    return page("Authorization error",
                f'<div class="box"><h1>Authorization error</h1><p>{html.escape(message)}</p></div>')
