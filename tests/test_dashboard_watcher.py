"""Tests for nuvel.dashboard.watcher."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nuvel.dashboard.watcher import RunWatcher
from nuvel.traces_cli import Run


def _write_run(path: Path, trace_id: str, ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"trace_id": trace_id, "session_id": "s", "event": "run_start",
         "timestamp": ts, "agent": "test_agent", "user_input": "hi"},
        {"trace_id": trace_id, "session_id": "s", "event": "run_end",
         "timestamp": ts, "duration_ms": 100, "llm_calls": 1, "tool_calls": 0,
         "total_tokens": 50},
    ]
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.mark.asyncio
async def test_watcher_emits_new_run_when_file_appears(tmp_path: Path) -> None:
    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    queue: asyncio.Queue[Run] = asyncio.Queue()
    task = asyncio.create_task(watcher.run(queue))

    # Give the watcher a beat to do its first scan (which establishes baseline).
    await asyncio.sleep(0.5)

    _write_run(tmp_path / "2026-05-15_new.jsonl", "newrun01", "2026-05-15T11:00:00+00:00")

    run = await asyncio.wait_for(queue.get(), timeout=3.0)
    assert run.trace_id == "newrun01"

    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_watcher_ignores_files_seen_in_baseline(tmp_path: Path) -> None:
    _write_run(tmp_path / "2026-05-15_pre.jsonl", "preexist", "2026-05-15T10:00:00+00:00")

    watcher = RunWatcher(sources=[tmp_path], poll_seconds=0.2)
    queue: asyncio.Queue[Run] = asyncio.Queue()
    task = asyncio.create_task(watcher.run(queue))

    await asyncio.sleep(0.5)

    # No new files. Queue should stay empty.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.6)

    watcher.stop()
    await asyncio.wait_for(task, timeout=2.0)
