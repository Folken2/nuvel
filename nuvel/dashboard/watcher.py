"""JSONL trace file watcher.

Polls the configured source directories at a fixed interval. When a file
appears or grows, re-parses it and emits any newly-seen `Run` objects to
an `asyncio.Queue` the SSE endpoint drains.

Polling over inotify because cross-platform behavior is consistent and
1s is well within HTMX's perceived-real-time threshold for a demo.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from nuvel.traces_cli import (
    Run,
    _iter_trace_files,
    _parse_file_runs,
)

logger = logging.getLogger(__name__)


class RunWatcher:
    """Polls trace dirs and emits newly-arrived runs onto a queue."""

    def __init__(self, sources: list[Path], poll_seconds: float = 1.0) -> None:
        self._sources = sources
        self._poll = poll_seconds
        # TODO: bound this set (e.g. LRU cap at 10k) once the dashboard is
        # used in long-running scenarios. For the demo / short sessions it
        # is fine to grow without bound.
        self._seen: set[str] = set()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def _key(self, run: Run) -> str:
        return run.trace_id or f"{run.session_id}:{run.started_at}"

    def _snapshot(self) -> dict[str, Run]:
        out: dict[str, Run] = {}
        for f in _iter_trace_files(self._sources):
            # keep_events=True so RunView.has_error correctly detects
            # streamed-in error runs at render time.
            for run in _parse_file_runs(f, keep_events=True):
                out[self._key(run)] = run
        return out

    async def run(self, queue: asyncio.Queue[Run]) -> None:
        # Baseline scan: anything present at startup is "already known".
        try:
            baseline = self._snapshot()
        except Exception:  # noqa: BLE001 — never crash the watcher
            logger.exception("watcher baseline scan failed")
            baseline = {}
        self._seen = set(baseline.keys())

        while not self._stop.is_set():
            try:
                # Wait either for the poll interval to elapse OR for stop() to fire.
                # Using wait_for makes stop() take effect immediately rather than
                # at the end of the next sleep.
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
                    break  # stop was set
                except asyncio.TimeoutError:
                    pass  # poll interval elapsed; do a scan

                current = self._snapshot()
            except Exception:  # noqa: BLE001
                logger.exception("watcher scan failed")
                continue

            new_keys = set(current.keys()) - self._seen
            for k in new_keys:
                await queue.put(current[k])
            self._seen |= new_keys
