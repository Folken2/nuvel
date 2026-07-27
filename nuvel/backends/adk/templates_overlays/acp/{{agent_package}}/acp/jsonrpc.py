"""Minimal JSON-RPC 2.0 framing over stdio for the Agent Client Protocol.

ACP speaks JSON-RPC 2.0 with **newline-delimited JSON**: the client writes
one JSON object per line to the agent's stdin and reads one object per line
from its stdout. stdout is therefore reserved for protocol traffic only —
every log line and stray ``print`` must go to stderr (the ACP entrypoint in
``__main__`` redirects ``sys.stdout`` to guarantee this).

No third-party dependencies: reads run on a thread via the default executor
so the asyncio event loop stays free to service ``session/cancel`` while a
prompt is in flight.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Optional, TextIO

# ── Standard JSON-RPC 2.0 error codes ────────────────────────────────
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def make_response(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def make_notification(method: str, params: Any) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def make_request(msg_id: Any, method: str, params: Any) -> dict:
    """An agent→client request (e.g. ``fs/read_text_file``) awaiting a response."""
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}


class StdioTransport:
    """Newline-delimited JSON-RPC transport over a pair of text streams.

    Reads from ``sys.stdin`` and writes to the *real* stdout captured before
    the ACP entrypoint reassigns ``sys.stdout`` to stderr. Writes are
    serialized with a lock so concurrent notifications never interleave.
    """

    def __init__(self, out: Optional[TextIO] = None, inp: Optional[TextIO] = None):
        self._out: TextIO = out if out is not None else sys.stdout
        self._in: TextIO = inp if inp is not None else sys.stdin
        self._write_lock = asyncio.Lock()

    async def read(self) -> Optional[dict]:
        """Return the next JSON message, or ``None`` at EOF.

        Blank lines are skipped; malformed lines are dropped rather than
        crashing the loop.
        """
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, self._in.readline)
            if line == "":
                return None  # EOF — the client closed the pipe.
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    async def write(self, message: dict) -> None:
        data = json.dumps(message, ensure_ascii=False)
        async with self._write_lock:
            self._out.write(data + "\n")
            self._out.flush()
