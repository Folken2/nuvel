"""Atomic JSON-backed cron job store.

All writes go through ``os.replace`` on a sibling temp file so an
interrupted write never corrupts ``jobs.json``. The directory is
configurable via ``NUVEL_CRON_DIR`` (default ``~/.nuvel/cron``).

Schema (per job, all timestamps are ISO-8601 UTC):

    {
        "id": "<uuid4>",
        "name": "...",
        "prompt": "...",
        "schedule": "every 1h",
        "status": "active" | "paused" | "completed" | "pending",
        "delivery": "local" | "origin" | "slack:<chan>" | "telegram:<chat>",
        "origin": {"platform": "slack", "channel": "C123", "thread_ts": "..."},
        "secrets": ["SLACK_TOKEN", ...] | null,  # declared env-var scope
        "created_at": "...",
        "next_run_at": "...",
        "last_run_at": "..." | null,
        "last_run_output_path": "..." | null,
        "last_error": "..." | null
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def cron_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get("NUVEL_CRON_DIR", "~/.nuvel/cron")))


def jobs_file() -> Path:
    return cron_dir() / "jobs.json"


def output_dir() -> Path:
    return cron_dir() / "output"


def lock_file() -> Path:
    return cron_dir() / ".tick.lock"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    cron_dir().mkdir(parents=True, exist_ok=True)
    output_dir().mkdir(parents=True, exist_ok=True)


# A process-wide RLock guards atomic read-modify-write sequences against
# concurrent calls from the same process (the file lock guards across ticks).
_RW_LOCK = threading.RLock()


def load_jobs() -> list[dict[str, Any]]:
    _ensure_dir()
    path = jobs_file()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("cron: failed to read %s", path)
        return []
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("cron: jobs.json is corrupt — starting fresh")
        return []
    if not isinstance(data, list):
        return []
    return data


def save_jobs(jobs: list[dict[str, Any]]) -> None:
    """Atomic write via temp file + ``os.replace``."""
    _ensure_dir()
    path = jobs_file()
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    payload = json.dumps(jobs, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def transaction():
    """Hold the in-process RLock around a load/mutate/save cycle."""
    with _RW_LOCK:
        yield


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def write_output(job_id: str, content: str) -> Path:
    """Write a job's run output to ``output/<job_id>/<ISO>.md``. Returns the path."""
    _ensure_dir()
    job_out = output_dir() / job_id
    job_out.mkdir(parents=True, exist_ok=True)
    # Filesystem-safe ISO: replace ':' with '-'.
    stamp = utcnow_iso().replace(":", "-")
    path = job_out / f"{stamp}.md"
    path.write_text(content, encoding="utf-8")
    return path


# ── Tick lock (cross-process) ────────────────────────────────────────


class TickLockBusy(RuntimeError):
    """Raised by :func:`acquire_tick_lock` if the lock is already held."""


@contextmanager
def acquire_tick_lock():
    """Best-effort exclusive file lock for the scheduler tick.

    Uses ``fcntl.flock`` on POSIX. On platforms without fcntl, falls back to
    a non-atomic ``O_EXCL`` lock file which is good enough for the MVP.
    """
    _ensure_dir()
    path = lock_file()
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — Windows fallback
        fcntl = None  # type: ignore[assignment]

    if fcntl is not None:
        # Open (or create) and try LOCK_EX|LOCK_NB.
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise TickLockBusy("tick lock held") from exc
            try:
                os.write(fd, str(os.getpid()).encode())
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)
    else:  # pragma: no cover
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise TickLockBusy("tick lock held") from exc
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            os.close(fd)
            try:
                os.unlink(path)
            except OSError:
                pass
