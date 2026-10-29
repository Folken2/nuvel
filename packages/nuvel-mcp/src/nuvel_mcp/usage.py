"""Usage records: one event per JSON-RPC request, written off the request path.

An event is ``{"at", "tenant", "transport", "method", "tool", "outcome",
"error_code", "duration_ms"}``. ``method`` and ``tool`` are names the server
knows or ``"unknown"`` (never caller-supplied text); ``outcome`` is ``ok``,
``tool_error`` (a tool answered ``isError``) or ``error`` (a JSON-RPC error).

:class:`UsageRecorder` queues events and a daemon thread writes them in
batches to a :class:`UsageStore`. A full queue or a failed write drops events:
they are counted and logged at most once a minute, and the request that
produced them is never slowed or failed.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class UsageStore(Protocol):
    def record_batch(self, events: list[dict]) -> None:
        """Store *events*; raise on failure (the recorder drops and counts them)."""
        ...


def _log(message: str) -> None:
    # stderr, like server.log: stdout is the stdio protocol channel.
    print(message, file=sys.stderr, flush=True)


class UsageRecorder:
    QUEUE_MAX = 10_000
    BATCH_MAX = 200
    FLUSH_INTERVAL = 1.0
    LOG_INTERVAL = 60.0

    def __init__(self, store: UsageStore, *, start: bool = True, clock=time.monotonic):
        self.store = store
        self.clock = clock
        self.dropped = 0
        self._queue: queue.Queue = queue.Queue(maxsize=self.QUEUE_MAX)
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self._last_log = None
        self._unlogged = 0
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, name="nuvel-mcp-usage", daemon=True)
            self._thread.start()

    def record(self, event: dict) -> None:
        """Queue *event*; never blocks and never raises."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            self._drop(1, "queue full")

    def _drop(self, count: int, why: str) -> None:
        self.dropped += count
        self._unlogged += count
        now = self.clock()
        if self._last_log is None or now - self._last_log >= self.LOG_INTERVAL:
            _log(f"usage: dropped {self._unlogged} event(s) ({why}); "
                 f"{self.dropped} dropped since start")
            self._last_log, self._unlogged = now, 0

    def _take(self, block: bool) -> list[dict]:
        batch = []
        deadline = self.clock() + self.FLUSH_INTERVAL
        while len(batch) < self.BATCH_MAX:
            try:
                if block and not batch:
                    remaining = max(0.0, deadline - self.clock())
                    batch.append(self._queue.get(timeout=remaining))
                else:
                    batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def flush(self) -> None:
        """Write everything queued so far."""
        while True:
            batch = self._take(block=False)
            if not batch:
                return
            self._write(batch)

    def _write(self, batch: list[dict]) -> None:
        with self._write_lock:
            try:
                self.store.record_batch(batch)
            except Exception as exc:  # noqa: BLE001 - usage must never take a request down
                self._drop(len(batch), f"write failed: {type(exc).__name__}")

    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._take(block=True)
            if batch:
                self._write(batch)

    def close(self, timeout: float = 5.0) -> None:
        """Stop the writer and flush what is left."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self.flush()
