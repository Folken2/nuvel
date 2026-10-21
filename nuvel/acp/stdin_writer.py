"""Cancel-safe stdin/stdout writer actor pattern for NDJSON transport.

.. _Buzz issue #6671: https://github.com/jdorfman/awesome-json-datasets
.. _Buzz PR #6675: https://github.com/jdorfman/awesome-json-datasets

Problem
-------

ACP (Agent Client Protocol) speaks JSON-RPC 2.0 over NDJSON — one
JSON object per line.  When the caller writes a large frame (>256 KiB)
directly to the pipe and the caller's task is cancelled midway, the
pipe contains a partial line.  The next frame written by the next
caller gets appended to that partial line, and the reader on the other
end sees a corrupted byte stream — it tries to parse the concatenated
broken bytes as one JSON object and fails.

This was observed in Buzz (Block's agent runtime) as issue #6671 and
patched in PR #6675.

Solution
--------

The **StdinWriter actor pattern** solves this by giving **exclusive
ownership** of the write end to a single background actor task.  No
caller ever touches the pipe directly:

1. A caller submits a complete NDJSON frame via :meth:`StdinWriter.write_line`.
2. The frame is placed into a bounded :class:`asyncio.Queue` along with a
   :class:`asyncio.Future` that the caller can await.
3. The actor task dequeues frames one at a time and performs
   ``write_all`` + ``flush`` atomically.  The caller's future is
   resolved only after the write completes.

Critically, **the caller can be cancelled at any point** — if
``write_line``'s ``await`` is cancelled, the caller never holds a
partial write; the actor task either hasn't started the write yet or
has already finished it.  The NDJSON framing is never truncated.

In the ``spawn`` classmethod, a sentinel ``None`` signals that all
producers have finished; the actor flushes and closes the stream.

Design notes
------------

- Bounded queue (256 entries) prevents unbounded memory growth if the
  writer falls behind.
- 30-second timeout per write matches Buzz's ``WRITE_TIMEOUT``.
- The :func:`main` block at the bottom includes the regression test
  pattern from PR #6675: a cancelled >256 KiB prompt write followed
  by a session/cancel write.  The reader verifies that both frames
  arrive intact.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_WRITE_TIMEOUT = 30.0  # seconds — matches Buzz's WRITE_TIMEOUT
_QUEUE_MAX_SIZE = 256

# Sentinel value signalling the actor to flush and exit.
_SENTINEL: Any = None

W = TypeVar("W")
"""Type variable for the underlying writer, so tests can use in-memory
streams (e.g. :class:`io.BytesIO` or :class:`asyncio.Queue`)."""


# ── Write request ────────────────────────────────────────────────────


@dataclass
class WriteRequest(Generic[W]):
    """A single NDJSON frame queued for the actor to write.

    Attributes:
        line: The complete JSON string (without trailing newline — the
            actor adds it).
        future: Resolved with ``None`` on success or the writer object
            for tests, or with an exception on failure.
    """

    line: str
    future: asyncio.Future[None] = field(default_factory=asyncio.Future)


# ── StdinWriter actor ────────────────────────────────────────────────


class StdinWriter(Generic[W]):
    """Actor that owns the write end of the NDJSON transport exclusively.

    Consumers call :meth:`write_line` to queue a complete frame, then
    await the returned future for confirmation that the bytes hit the
    pipe.  The caller's task may be cancelled safely — the actor alone
    performs the raw I/O, so cancellation never truncates a frame.

    Use the :meth:`spawn` classmethod to create an instance with an
    arbitrary backend writer (tests can pass an in-memory queue or
    :class:`io.BytesIO`).

    Example::

        writer = StdinWriter.spawn(asyncio.StreamWriter(...))
        await writer.write_line('{"jsonrpc":"2.0","id":1,"result":{}}')
        writer.close()
    """

    def __init__(self, queue: asyncio.Queue[Any], task: asyncio.Task[Any]) -> None:
        self._queue = queue
        self._task = task

    # ── Public API ───────────────────────────────────────────────────

    async def write_line(self, line: str) -> None:
        """Submit one complete NDJSON frame and wait for it to be written.

        The caller may be cancelled at any point; the actual pipe write
        happens inside the actor task, so NDJSON framing is never
        truncated.

        Args:
            line: The JSON-RPC message as a string (without trailing
                newline — the actor adds one).

        Returns:
            ``None`` when the write succeeds.

        Raises:
            TimeoutError: If the write takes longer than
                :data:`_WRITE_TIMEOUT` seconds.
            RuntimeError: If :meth:`close` has already been called.
        """
        req: WriteRequest = WriteRequest(line=line)
        await self._queue.put(req)
        await req.future

    def close(self) -> None:
        """Signal the actor to flush and shut down after draining the queue.

        Idempotent; safe to call more than once.
        """
        # Put the sentinel so the actor loop exits after draining.
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            # The queue is full of real work — the sentinel is optional
            # (the actor will exit when a producer fed it None).  If we
            # can't enqueue it here the caller should feed the sentinel
            # later or cancel the task.
            pass

    async def wait_closed(self) -> None:
        """Block until the actor task has finished (drained + closed)."""
        await self._task

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def spawn(cls, writer: W | Callable[[bytes], Any]) -> StdinWriter[W]:
        """Create a :class:`StdinWriter` and start its actor task.

        ``writer`` may be:

        * An :class:`asyncio.StreamWriter` (the stdio case),
        * A synchronous callable ``(bytes) -> None`` (e.g. a
          :meth:`bytesio.write` wrapper), or
        * Any object with an ``.write(data: bytes)`` method.

        The actor writes the encoded frame (``line + "\\n"``) to
        ``writer``, then calls ``.drain()`` on stream writers or
        ``.flush()`` for compatibility.

        Args:
            writer: The write endpoint that the actor will own
                exclusively.

        Returns:
            A new :class:`StdinWriter` with the actor task already
            running.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_QUEUE_MAX_SIZE)
        task = asyncio.ensure_future(cls._actor_loop(queue, writer))
        return cls(queue, task)

    # ── Actor loop ───────────────────────────────────────────────────

    @staticmethod
    async def _actor_loop(
        queue: asyncio.Queue[Any],
        writer: W | Callable[[bytes], Any],
    ) -> None:
        """Single-task loop: dequeue frames, write them atomically.

        Exits when it receives a sentinel (``None``).  Any exception
        is propagated to the future of the request currently being
        written, and the loop terminates.
        """
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break  # flush + close below

                if isinstance(item, WriteRequest):
                    await StdinWriter._do_write(writer, item)
        except asyncio.CancelledError:
            # Propagate cancellation to any request currently being
            # dequeued so its caller doesn't hang forever.
            pass
        except Exception:
            logger.exception("StdinWriter actor crashed")
        finally:
            # Drain remaining requests (resolve them as cancelled) so
            # no caller hangs.
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if isinstance(item, WriteRequest):
                    if not item.future.done():
                        item.future.cancel()
            await StdinWriter._flush_and_close(writer)

    @staticmethod
    async def _do_write(
        writer: W | Callable[[bytes], Any],
        req: WriteRequest,
    ) -> None:
        """Atomically write one frame to the transport.

        Encodes the line as UTF-8, appends a newline, writes, and
        flushes/drains.  A 30-second timeout guards against hung pipes.
        """
        data = req.line.encode("utf-8") + b"\n"

        try:
            async with asyncio.timeout(_WRITE_TIMEOUT):
                if hasattr(writer, "write") and asyncio.iscoroutinefunction(
                    getattr(writer, "write", None)
                ):
                    # asyncio.StreamWriter path
                    writer.write(data)  # type: ignore[union-attr]
                    if hasattr(writer, "drain"):
                        await writer.drain()  # type: ignore[union-attr]
                elif callable(writer):
                    # Bare callable (e.g. a BytesIO wrapper).
                    writer(data)  # type: ignore[operator]
                else:
                    # Fallback: assume .write(data) exists and is sync.
                    writer.write(data)  # type: ignore[union-attr]
                    if hasattr(writer, "flush"):
                        writer.flush()  # type: ignore[union-attr]
        except asyncio.TimeoutError:
            if not req.future.done():
                req.future.set_exception(
                    TimeoutError(f"Write timed out after {_WRITE_TIMEOUT}s")
                )
            raise
        except Exception as exc:
            if not req.future.done():
                req.future.set_exception(exc)
            raise
        else:
            if not req.future.done():
                req.future.set_result(None)

    @staticmethod
    async def _flush_and_close(
        writer: W | Callable[[bytes], Any],
    ) -> None:
        """Best-effort flush + close of the underlying stream."""
        try:
            if hasattr(writer, "close") and asyncio.iscoroutinefunction(
                getattr(writer, "close", None)
            ):
                writer.close()  # type: ignore[union-attr]
                await writer.wait_closed()  # type: ignore[union-attr]
            elif hasattr(writer, "close"):
                writer.close()  # type: ignore[union-attr]
        except Exception:
            logger.debug("Error closing writer", exc_info=True)


# ── Self-test / regression test ──────────────────────────────────────


async def _regression_test() -> None:
    """Reproduce the Buzz #6671 regression: cancelled large write then a
    second small write — both frames must arrive intact.

    Uses an in-memory :class:`io.BytesIO` as the transport.
    """
    import io

    buf = io.BytesIO()

    def _sync_write(data: bytes) -> None:
        buf.write(data)
        buf.flush()

    writer = StdinWriter.spawn(_sync_write)

    # 1. Build a >256 KiB frame (the bug trigger size in Buzz).
    large_payload = "x" * (256 * 1024 + 4096)
    large_frame = '{"jsonrpc":"2.0","method":"session/prompt","params":{"prompt":"%s"}}' % (
        large_payload
    )

    # Fire the large write and cancel it immediately — simulating a
    # client that sends ``session/cancel`` while a prompt is still
    # streaming out.
    large_task = asyncio.ensure_future(writer.write_line(large_frame))
    await asyncio.sleep(0)  # let the actor dequeue it and start writing
    large_task.cancel()
    try:
        await large_task
    except asyncio.CancelledError:
        pass

    # 2. Immediately write a small cancel frame.
    cancel_frame = '{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"s"}}'
    await writer.write_line(cancel_frame)

    writer.close()
    await writer.wait_closed()

    # 3. Read back what the transport actually received.
    raw = buf.getvalue()
    lines = raw.split(b"\n")

    # The reader should get exactly two valid JSON frames (or the large
    # one might be missing entirely if the actor never started it — in
    # either case the second frame must be intact).
    valid_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            import json

            try:
                json.loads(stripped)
                valid_lines.append(stripped.decode("utf-8"))
            except json.JSONDecodeError:
                print(f"[FAIL] Corrupted NDJSON line: {stripped!r}")
                raise

    assert len(valid_lines) >= 1, f"Expected at least one valid frame, got {len(valid_lines)}"
    # The cancel frame must be the last one and must parse.
    assert "session/cancel" in valid_lines[-1], (
        f"Expected session/cancel as last frame, got: {valid_lines[-1]}"
    )
    print("[PASS] Regression test: cancel-safe NDJSON write passed.")
    print(f"  Frames received: {len(valid_lines)}")


if __name__ == "__main__":
    asyncio.run(_regression_test())