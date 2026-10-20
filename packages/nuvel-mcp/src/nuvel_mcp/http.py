"""Streamable HTTP transport for the Skills MCP server (the ``http`` extra).

This is the only module that imports the ``mcp`` SDK; the stdio server and the
rest of the package stay dependency-free. The SDK's low-level ``Server`` speaks
the protocol and delegates ``tools/list`` / ``tools/call`` to the same
:class:`SkillsMCPServer` handlers stdio uses.

In front of it, :class:`BodyLimitAuthMiddleware` authenticates every request,
caps the request body and rejects malformed JSON before the SDK sees it. The
authenticated principal is carried on the ASGI scope and set as
:data:`current_principal` around both the request and the tool call itself
(tool handlers run in a worker thread), so ``record_run_trace`` and
``check_skill_health`` are always scoped to the caller.
"""

from __future__ import annotations

import contextlib
import json
import urllib.parse
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, MutableMapping
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.routing import Route

from nuvel_mcp.auth import LOOPBACK_HOSTS, Authenticator, current_principal
from nuvel_mcp.server import INVALID_PARAMS, SERVER_INFO, McpError, SkillsMCPServer, log

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MAX_BODY_BYTES = 65536
MCP_PATH = "/mcp"
# ASGI scope key carrying the authenticated principal to the tool handler.
PRINCIPAL_SCOPE_KEY = "nuvel_mcp.principal"


async def _reject(send: Send, status: int, error: str, extra: list | None = None) -> None:
    """Send a small fixed JSON error. Never includes request data."""
    body = json.dumps({"error": error}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _hostname(value: str) -> str | None:
    """Host name of a ``Host`` header value (``[::1]:8765`` -> ``::1``)."""
    try:
        return urllib.parse.urlsplit(f"//{value}").hostname
    except ValueError:
        return None


def _origin_allowed(origin: str, hostnames: frozenset[str]) -> bool:
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and parts.hostname in hostnames


class BodyLimitAuthMiddleware:
    """Validate Host/Origin, authenticate, cap the body and require valid JSON.

    With ``loopback_only`` (a loopback bind) a request whose ``Host`` is not a
    loopback name or one of ``allowed_hosts`` gets 421, and one whose
    ``Origin`` is present and not ``http(s)://`` one of those gets 403. Names
    match case-insensitively on any port. That stops DNS-rebinding pages,
    which the browser treats as same-origin, from reaching the server, with or
    without an authenticator; ``allowed_hosts`` admits the public name a
    reverse proxy forwards.

    Order matters: authentication runs before any body byte is read, so an
    unauthenticated caller can't make the server buffer anything. A request
    carrying more than one ``Authorization`` header is rejected outright rather
    than letting the authenticator pick one.
    """

    def __init__(self, app: ASGIApp, authenticator: Authenticator | None,
                 max_body_bytes: int = MAX_BODY_BYTES, loopback_only: bool = True,
                 allowed_hosts: Iterable[str] = ()) -> None:
        self.app = app
        self.authenticator = authenticator
        self.max_body_bytes = max_body_bytes
        self.loopback_only = loopback_only
        self.hostnames = LOOPBACK_HOSTS | {h.lower() for h in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        if self.loopback_only:
            headers = Headers(raw=list(raw_headers))
            hosts = headers.getlist("host")
            if len(hosts) != 1 or _hostname(hosts[0]) not in self.hostnames:
                await _reject(send, 421, "invalid host")
                return
            origins = headers.getlist("origin")
            if len(origins) > 1 or (origins and not _origin_allowed(origins[0], self.hostnames)):
                await _reject(send, 403, "invalid origin")
                return
        if sum(1 for k, _ in raw_headers if k.lower() == b"authorization") > 1:
            await _reject(send, 401, "unauthorized", [(b"www-authenticate", b"Bearer")])
            return
        principal: str | None = None
        if self.authenticator is not None:
            principal = self.authenticator.authenticate(Headers(raw=list(raw_headers)))
            if principal is None:
                await _reject(send, 401, "unauthorized", [(b"www-authenticate", b"Bearer")])
                return

        if scope.get("method") == "POST":
            body = await self._read_body(scope, receive, send)
            if body is None:
                return
            receive = _replay(body, receive)

        scope[PRINCIPAL_SCOPE_KEY] = principal
        token = current_principal.set(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            current_principal.reset(token)

    async def _read_body(self, scope: Scope, receive: Receive, send: Send) -> bytes | None:
        """Read the whole body (bounded), or send the error and return None."""
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            if not declared.isdigit():
                await _reject(send, 400, "invalid content-length")
                return None
            if int(declared) > self.max_body_bytes:
                await _reject(send, 413, "request body too large")
                return None

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:  # chunked or lying Content-Length
                await _reject(send, 413, "request body too large")
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        try:
            json.loads(body)
        except ValueError:
            await _reject(send, 400, "invalid JSON")
            return None
        return body


def _replay(body: bytes, receive: Receive) -> Receive:
    """A ``receive`` that yields the already-read body once, then defers."""
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _error_result(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _build_mcp_server(server: SkillsMCPServer) -> Server:
    app: Server = Server(SERVER_INFO["name"], version=SERVER_INFO.get("version"))

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        listed = server.handle_tools_list({})["tools"]
        return [types.Tool.model_validate(tool) for tool in listed]

    # The shared handler validates arguments itself, without echoing them.
    @app.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        request = app.request_context.request
        principal = request.scope.get(PRINCIPAL_SCOPE_KEY) if request is not None else None

        def run() -> dict[str, Any]:
            token = current_principal.set(principal)
            try:
                return server.handle_tools_call({"name": name, "arguments": arguments})
            except McpError as exc:
                # Invalid-params messages are written for the caller; anything
                # else (e.g. a storage error naming a server path) is not.
                if exc.code == INVALID_PARAMS:
                    return _error_result(exc.message)
                log(f"Tool {name} failed: {exc.message}")
                return _error_result("Internal error")
            except Exception as exc:
                log(f"Tool {name} failed: {type(exc).__name__}")
                return _error_result("Internal error")
            finally:
                current_principal.reset(token)

        result = await anyio.to_thread.run_sync(run)
        return types.CallToolResult.model_validate(result)

    return app


def build_app(server: SkillsMCPServer, authenticator: Authenticator | None,
              max_body_bytes: int = MAX_BODY_BYTES, *,
              bind_host: str = "127.0.0.1",
              allowed_hosts: Iterable[str] = ()) -> Starlette:
    """The ASGI app serving ``server`` at ``/mcp`` behind ``authenticator``.

    ``authenticator=None`` disables authentication (local use only; the CLI
    refuses it on a non-loopback host). When ``bind_host`` is a loopback name,
    ``Host`` and ``Origin`` must be loopback too, or one of ``allowed_hosts``
    (the public name a reverse proxy forwards): DNS-rebinding protection.
    Sessions are stateless and responses are plain JSON: every request is
    authenticated on its own.

    Puts ``server`` in remote mode: ``check_skill_health`` then returns only the
    caller's run summary, nothing from the local feedback store.
    """
    server.health_runs_only = True
    manager = StreamableHTTPSessionManager(
        app=_build_mcp_server(server), json_response=True, stateless=True,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    app = Starlette(routes=[Route(MCP_PATH, endpoint=_MCPEndpoint(manager))],
                    lifespan=lifespan)
    app.add_middleware(BodyLimitAuthMiddleware, authenticator=authenticator,
                       max_body_bytes=max_body_bytes,
                       loopback_only=bind_host in LOOPBACK_HOSTS,
                       allowed_hosts=allowed_hosts)
    return app


class _MCPEndpoint:
    """ASGI app (a class instance, so Starlette's ``Route`` passes it raw ASGI)."""

    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self._manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._manager.handle_request(scope, receive, send)


def serve_http(server: SkillsMCPServer, host: str, port: int,
               authenticator: Authenticator | None,
               allowed_hosts: Iterable[str] = ()) -> None:
    """Serve ``server`` over Streamable HTTP with uvicorn until interrupted."""
    import uvicorn

    app = build_app(server, authenticator, bind_host=host, allowed_hosts=allowed_hosts)
    uvicorn.run(app, host=host, port=port, log_level="info")
