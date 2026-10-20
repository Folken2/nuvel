"""Minimal JSON-RPC 2.0 framing over stdio for the Agent Client Protocol.

ACP speaks JSON-RPC 2.0 with **newline-delimited JSON**: the client writes
one JSON object per line to the agent's stdin and reads one object per line
from its stdout. stdout is therefore reserved for protocol traffic only —
every log line and stray ``print`` must go to stderr (the ACP entrypoint in
``__main__`` redirects ``sys.stdout`` to guarantee this).

No third-party dependencies: reads run on a thread via the default executor
so the asyncio event loop stays free to service ``session/cancel`` while a
prompt is in flight.

Writes are cancel-safe: :class:`StdioTransport` routes every frame through a
:class:`StdinWriter` actor that owns the write end of the pipe exclusively.
A cancelled caller never leaves a partial NDJSON frame (the "Buzz #6675"
actor pattern), so a ``session/cancel`` racing an in-flight ``session/update``
can't fuse two frames into one corrupt byte stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Optional, TextIO

logger = logging.getLogger(__name__)

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


# ── Cancel-safe writer actor ─────────────────────────────────────────

_WRITE_TIMEOUT = 30.0  # seconds per write
_QUEUE_MAX_SIZE = 256

# Sentinel value signalling the actor to flush and exit.
_SENTINEL: Any = None


@dataclass
class WriteRequest:
    """A single NDJSON frame queued for the actor to write.

    Attributes:
        line: The complete JSON string (without trailing newline — the
            actor adds it).
        future: Resolved with ``None`` on success, or with an exception on
            failure (including the 30-second timeout).
    """

    line: str
    future: asyncio.Future[None] = field(default_factory=asyncio.Future)


class StdinWriter:
    """Actor that owns the write end of the NDJSON transport exclusively.

    Consumers call :meth:`write_line` to queue a complete frame, then await
    the returned future for confirmation the bytes hit the pipe. The caller's
    task may be cancelled safely — the actor alone performs the raw I/O, so
    cancellation never truncates a frame.

    Use :meth:`spawn` to create an instance bound to a text stream (the
    transport's *real* stdout, captured before the entrypoint reassigned
    ``sys.stdout``).
    """

    def __init__(
        self,
        out: TextIO,
        queue: asyncio.Queue[Any],
        task: asyncio.Task[Any],
    ) -> None:
        self._out = out
        self._queue = queue
        self._task = task

    async def write_line(self, line: str) -> None:
        """Submit one complete NDJSON frame and wait for it to be written.

        The caller may be cancelled at any point; the actual write happens
        inside the actor task, so NDJSON framing is never truncated.

        Raises:
            TimeoutError: If the write takes longer than
                :data:`_WRITE_TIMEOUT` seconds.
        """
        req = WriteRequest(line=line)
        await self._queue.put(req)
        await req.future

    def close(self) -> None:
        """Signal the actor to flush and shut down after draining the queue.

        Idempotent; safe to call more than once.
        """
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            # Queue is full of real work; the sentinel is optional — the actor
            # will exit once a producer feeds it ``None``.
            pass

    async def wait_closed(self) -> None:
        """Block until the actor task has finished (drained + flushed)."""
        await self._task

    @classmethod
    def spawn(cls, out: TextIO) -> "StdinWriter":
        """Create a :class:`StdinWriter` and start its actor task.

        Args:
            out: The text stream (real stdout) the actor owns exclusively.

        Returns:
            A new :class:`StdinWriter` with the actor task already running.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        task = asyncio.ensure_future(cls._actor_loop(out, queue))
        return cls(out, queue, task)

    @staticmethod
    async def _actor_loop(out: TextIO, queue: asyncio.Queue[Any]) -> None:
        """Single-task loop: dequeue frames and write them atomically."""
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break  # flush below
                if isinstance(item, WriteRequest):
                    await StdinWriter._do_write(out, item)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("StdinWriter actor crashed")
        finally:
            # Resolve any remaining queued requests so no caller hangs.
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, WriteRequest) and not item.future.done():
                    item.future.cancel()
            try:
                out.flush()
            except Exception:  # noqa: BLE001
                logger.debug("Error flushing stdout", exc_info=True)

    @staticmethod
    async def _do_write(out: TextIO, req: WriteRequest) -> None:
        """Atomically write one frame (the full line + flush) to the stream.

        A 30-second timeout guards against a hung pipe.
        """
        try:
            async with asyncio.timeout(_WRITE_TIMEOUT):
                out.write(req.line + "\n")
                out.flush()
        except asyncio.TimeoutError:
            if not req.future.done():
                req.future.set_exception(
                    TimeoutError(f"Write timed out after {_WRITE_TIMEOUT}s")
                )
            raise
        except Exception as exc:  # noqa: BLE001
            if not req.future.done():
                req.future.set_exception(exc)
            raise
        else:
            if not req.future.done():
                req.future.set_result(None)


class StdioTransport:
    """Newline-delimited JSON-RPC transport over a pair of text streams.

    Reads from ``sys.stdin`` and writes to the *real* stdout captured before
    the ACP entrypoint reassigns ``sys.stdout`` to stderr. Writes are routed
    through a :class:`StdinWriter` actor, so concurrent notifications never
    interleave and a cancelled caller never leaves a partial frame.
    """

    def __init__(self, out: Optional[TextIO] = None, inp: Optional[TextIO] = None):
        self._out: TextIO = out if out is not None else sys.stdout
        self._in: TextIO = inp if inp is not None else sys.stdin
        # Spawned lazily on the first write, which always happens inside the
        # running event loop (the transport is constructed before
        # ``asyncio.run`` in ``__main__``).
        self._writer: Optional[StdinWriter] = None

    def _ensure_writer(self) -> StdinWriter:
        if self._writer is None:
            self._writer = StdinWriter.spawn(self._out)
        return self._writer

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
        await self._ensure_writer().write_line(data)

    def close(self) -> None:
        """Flush and stop the writer actor after draining queued frames."""
        if self._writer is not None:
            self._writer.close()

    async def wait_closed(self) -> None:
        """Block until the writer actor has drained and flushed."""
        if self._writer is not None:
            await self._writer.wait_closed()
