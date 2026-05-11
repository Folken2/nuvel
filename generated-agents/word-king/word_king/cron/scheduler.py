"""Background tick scheduler.

Runs every 60s. On each tick:

1. Acquire the cross-process tick lock (skip the tick if held).
2. Load ``jobs.json``, find jobs where ``next_run_at <= now`` and
   ``status == "active"`` and not already in flight.
3. For each due job, spawn a fresh ADK session, run the prompt, capture
   the final response, write it to the output file, deliver per the
   job's ``delivery`` field, and persist updated metadata.

The scheduler is opt-in: ``NUVEL_CRON_ENABLED=1`` to start it. The
FastAPI lifespan in ``run_adk.py`` is responsible for calling
:func:`start_scheduler`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from . import storage
from .delivery import deliver, is_silent, strip_silent
from .schedule import compute_next_run, parse_schedule
from .service import NUVEL_CRON_RUNNING_ENV, get_service

logger = logging.getLogger(__name__)

ENV_ENABLED = "NUVEL_CRON_ENABLED"
ENV_TICK_SECONDS = "NUVEL_CRON_TICK_SECONDS"
DEFAULT_TICK_SECONDS = 60.0


# Type alias: the runner-invoker signature. Returns the agent's final text.
RunnerInvoker = Callable[[str, str], Awaitable[str]]


def is_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").lower() in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def run_one_job(
    job: dict[str, Any],
    invoker: RunnerInvoker,
) -> dict[str, Any]:
    """Execute a single job once. Returns the updated job dict.

    The scheduler is responsible for persistence; this function only does
    the run + delivery + metadata mutation in-memory.
    """
    name = job.get("name", "")
    prompt = job.get("prompt", "")
    delivery = job.get("delivery", "local")
    origin = job.get("origin")
    job_id = job.get("id", "")

    # Recursion guard for the cronjob tool.
    prev = os.environ.get(NUVEL_CRON_RUNNING_ENV)
    os.environ[NUVEL_CRON_RUNNING_ENV] = "1"
    try:
        try:
            response = await invoker(job_id, prompt)
        except Exception as exc:
            logger.exception("cron: job %s invocation failed", job_id)
            job["last_error"] = str(exc)
            response = f"[error] {exc}"
        else:
            job["last_error"] = None
    finally:
        if prev is None:
            os.environ.pop(NUVEL_CRON_RUNNING_ENV, None)
        else:
            os.environ[NUVEL_CRON_RUNNING_ENV] = prev

    # Always write the output file (even on error, for audit).
    try:
        path = storage.write_output(
            job_id, _format_output_file(job, response),
        )
        job["last_run_output_path"] = str(path)
    except OSError:
        logger.exception("cron: failed to write output for %s", job_id)

    # Deliver (silent prefix is honored inside `deliver`).
    delivery_text = strip_silent(response) if is_silent(response) else response
    await deliver(
        name=name, response=delivery_text, delivery=delivery, origin=origin,
    )

    job["last_run_at"] = _now().isoformat()

    # Compute next run.
    try:
        parsed = parse_schedule(job.get("schedule", ""))
        nxt = compute_next_run(
            parsed, now=_now(),
            last_run_at=_parse_iso(job["last_run_at"]),
        )
        if nxt is None and not parsed.is_recurring:
            job["status"] = "completed"
            job["next_run_at"] = job["last_run_at"]
        else:
            job["next_run_at"] = (nxt or _now()).isoformat()
    except Exception:
        logger.exception("cron: failed to recompute schedule for %s", job_id)
        job["status"] = "paused"

    return job


def _format_output_file(job: dict[str, Any], response: str) -> str:
    return (
        f"# Cron run: {job.get('name')}\n\n"
        f"- job_id: `{job.get('id')}`\n"
        f"- ran_at: {_now().isoformat()}\n"
        f"- schedule: `{job.get('schedule')}`\n"
        f"- delivery: `{job.get('delivery')}`\n\n"
        "## Prompt\n\n"
        f"{job.get('prompt', '')}\n\n"
        "## Response\n\n"
        f"{response}\n"
    )


async def tick_once(invoker: RunnerInvoker, *, in_flight: set[str]) -> int:
    """Run one tick. Returns the number of jobs processed.

    Non-blocking on overlap: if the file lock is held, returns 0 immediately.
    """
    try:
        with storage.acquire_tick_lock():
            return await _do_tick(invoker, in_flight=in_flight)
    except storage.TickLockBusy:
        logger.debug("cron: tick lock busy, skipping")
        return 0


async def _do_tick(invoker: RunnerInvoker, *, in_flight: set[str]) -> int:
    now = _now()
    service = get_service()
    jobs = service.list_jobs()
    due: list[dict[str, Any]] = []
    for j in jobs:
        if j.get("status") != "active":
            continue
        if j.get("id") in in_flight:
            continue
        nxt = _parse_iso(j.get("next_run_at"))
        if nxt is None or nxt <= now:
            due.append(j)

    if not due:
        return 0

    async def _wrap(job: dict[str, Any]) -> None:
        jid = job["id"]
        in_flight.add(jid)
        try:
            updated = await run_one_job(dict(job), invoker)
            with storage.transaction():
                all_jobs = storage.load_jobs()
                for i, existing in enumerate(all_jobs):
                    if existing.get("id") == jid:
                        all_jobs[i] = updated
                        break
                storage.save_jobs(all_jobs)
        finally:
            in_flight.discard(jid)

    await asyncio.gather(*[_wrap(j) for j in due], return_exceptions=True)
    return len(due)


# ── Public lifespan helpers ──────────────────────────────────────────


class _SchedulerHandle:
    """Tracks the background tick task started by :func:`start_scheduler`."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.in_flight: set[str] = set()
        self.stop_event: asyncio.Event | None = None


_HANDLE = _SchedulerHandle()


async def _loop(invoker: RunnerInvoker, tick_seconds: float) -> None:
    assert _HANDLE.stop_event is not None
    logger.info("cron: scheduler started (tick=%.0fs)", tick_seconds)
    while not _HANDLE.stop_event.is_set():
        try:
            await tick_once(invoker, in_flight=_HANDLE.in_flight)
        except Exception:
            logger.exception("cron: tick raised")
        try:
            await asyncio.wait_for(_HANDLE.stop_event.wait(), timeout=tick_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("cron: scheduler stopped")


def start_scheduler(invoker: RunnerInvoker) -> None:
    """Kick off the background tick loop. No-op if already running or disabled."""
    if not is_enabled():
        logger.info("cron: NUVEL_CRON_ENABLED unset — scheduler disabled")
        return
    if _HANDLE.task is not None and not _HANDLE.task.done():
        return
    tick_seconds = float(os.environ.get(ENV_TICK_SECONDS, str(DEFAULT_TICK_SECONDS)))
    _HANDLE.stop_event = asyncio.Event()
    _HANDLE.task = asyncio.create_task(_loop(invoker, tick_seconds))


async def stop_scheduler() -> None:
    if _HANDLE.task is None:
        return
    if _HANDLE.stop_event is not None:
        _HANDLE.stop_event.set()
    try:
        await asyncio.wait_for(_HANDLE.task, timeout=5.0)
    except asyncio.TimeoutError:
        _HANDLE.task.cancel()
    _HANDLE.task = None
    _HANDLE.stop_event = None


def make_default_invoker(
    *, runner: Any, app_name: str, user_id: str = "cron"
) -> RunnerInvoker:
    """Build an invoker that runs the prompt in a fresh per-job session.

    The invoker delegates to ``gateways._common.invoke_agent`` if available;
    otherwise it falls back to a minimal direct ``runner.run_async`` call.
    """
    async def _invoker(job_id: str, prompt: str) -> str:
        session_id = f"cron:{job_id}:{uuid.uuid4().hex[:8]}"
        try:
            from word_king.gateways._common import (
                ensure_session, invoke_agent,
            )
            await ensure_session(runner.session_service, app_name, user_id, session_id)
            reply = await invoke_agent(runner, user_id, session_id, prompt)
            return reply.text
        except ImportError:
            # Gateway-base overlay not active; fall back to direct ADK runner.
            from google.genai import types as genai_types  # type: ignore[import-not-found]
            try:
                existing = await runner.session_service.get_session(
                    app_name=app_name, user_id=user_id, session_id=session_id,
                )
                if existing is None:
                    await runner.session_service.create_session(
                        app_name=app_name, user_id=user_id,
                        session_id=session_id, state={},
                    )
            except Exception:
                pass
            new_message = genai_types.Content(
                role="user", parts=[genai_types.Part(text=prompt)],
            )
            texts: list[str] = []
            async for event in runner.run_async(
                user_id=user_id, session_id=session_id, new_message=new_message,
            ):
                if getattr(event, "author", None) == "user":
                    continue
                content = getattr(event, "content", None)
                for part in (getattr(content, "parts", None) or []):
                    piece = getattr(part, "text", None)
                    if piece:
                        texts.append(piece)
            return texts[-1] if texts else ""

    return _invoker
