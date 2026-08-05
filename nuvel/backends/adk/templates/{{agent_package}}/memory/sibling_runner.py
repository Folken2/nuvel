"""
Fire-and-forget sibling-agent runner.

Owns the lifecycle for throwaway "judge" runs spawned after a parent turn:
per-fork throwaway session service, ``Runner`` construction, the
``asyncio.create_task`` bookkeeping, and an exception-swallowed drive loop.
Callers hand over a pre-built ``Agent`` (with whatever restricted toolset /
whitelist they enforce) plus the parent's ``(app_name, user_id)`` — the
sibling session is created under those identifiers so any memory writes land
where the parent's next-turn recall will surface them.

Registered as an ADK ``BasePlugin`` purely for its ``close()`` hook, which
drains in-flight siblings at shutdown (default ~4s) so writes from a turn
still in flight at SIGTERM aren't lost. The drain budget is capped under
ADK's 5s plugin-close budget so ADK never cancels the drain and re-raises it
as a user-visible error.

Opt-in behavior is decided by the caller (``review_fork``); this runner is
always safe to leave in the plugin chain — with nothing spawned it does
nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, Optional

from google.adk.plugins.base_plugin import BasePlugin

logger = logging.getLogger(__name__)

ENV_DRAIN_TIMEOUT = "NUVEL_MEMORY_SIBLING_DRAIN_TIMEOUT"
DEFAULT_DRAIN_TIMEOUT = 4.0
MAX_DRAIN_TIMEOUT = 4.5  # stay strictly under ADK's 5s plugin-close budget


def _resolve_drain_timeout(override: float | None) -> float:
    if override is not None:
        return min(MAX_DRAIN_TIMEOUT, max(0.0, float(override)))
    raw = os.environ.get(ENV_DRAIN_TIMEOUT)
    if raw is None:
        return DEFAULT_DRAIN_TIMEOUT
    try:
        return min(MAX_DRAIN_TIMEOUT, max(0.0, float(raw)))
    except ValueError:
        return DEFAULT_DRAIN_TIMEOUT


def _build_runner(*, agent: Any, app_name: str, session_service: Any, memory_service: Any):
    """Construct a throwaway Runner. Broken out so tests can monkeypatch it."""
    from google.adk.runners import Runner

    return Runner(
        app_name=app_name,
        agent=agent,
        session_service=session_service,
        memory_service=memory_service,
    )


class SiblingRunner(BasePlugin):
    """Schedules and drains fire-and-forget sibling-agent runs."""

    def __init__(self, *, drain_timeout: float | None = None) -> None:
        super().__init__(name="sibling_runner")
        self._pending: set[asyncio.Task] = set()
        self._drain_timeout = _resolve_drain_timeout(drain_timeout)

    def spawn(
        self,
        *,
        agent: Any,
        prompt: str,
        app_name: str,
        user_id: str,
        memory_service: Any = None,
        on_event: Callable[[Any], None] | None = None,
        log_prefix: str = "sibling_runner",
        session_state: Optional[dict] = None,
    ) -> asyncio.Task:
        """Schedule a fire-and-forget run of ``agent`` against ``prompt``.

        Returns the ``asyncio.Task`` so tests can await it; production callers
        discard the return value. The parent's reply is never blocked.
        """
        task = asyncio.create_task(
            self._run_one(
                agent=agent,
                prompt=prompt,
                app_name=app_name,
                user_id=user_id,
                memory_service=memory_service,
                on_event=on_event,
                log_prefix=log_prefix,
                session_state=session_state,
            )
        )
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return task

    async def _run_one(
        self,
        *,
        agent: Any,
        prompt: str,
        app_name: str,
        user_id: str,
        memory_service: Any,
        on_event: Callable[[Any], None] | None,
        log_prefix: str,
        session_state: Optional[dict],
    ) -> None:
        from google.adk.sessions import InMemorySessionService
        from google.genai.types import Content, Part

        session_service = InMemorySessionService()
        try:
            runner = _build_runner(
                agent=agent,
                app_name=app_name,
                session_service=session_service,
                memory_service=memory_service,
            )
            session = await session_service.create_session(
                app_name=app_name, user_id=user_id, state=session_state or {}
            )
            message = Content(role="user", parts=[Part(text=prompt)])
            async for event in runner.run_async(
                user_id=user_id, session_id=session.id, new_message=message
            ):
                if on_event is not None:
                    on_event(event)
        except Exception:
            logger.exception("%s: sibling run failed", log_prefix)

    async def close(self) -> None:
        """Drain in-flight siblings so their memory writes still land."""
        if not self._pending:
            return
        pending = list(self._pending)
        try:
            _done, not_done = await asyncio.wait(pending, timeout=self._drain_timeout)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning(
                "sibling_runner: close cancelled — %d in-flight task(s) dropped",
                len(pending),
            )
            return
        if not_done:
            logger.warning(
                "sibling_runner: drain timed out — %d/%d tasks unfinished in %.1fs",
                len(not_done), len(pending), self._drain_timeout,
            )


# Process-wide handle — imported by review_fork and registered in the plugin
# chain (plugins/__init__.py) so ADK drives its close() at shutdown.
SIBLING_RUNNER = SiblingRunner()


__all__ = ["SiblingRunner", "SIBLING_RUNNER", "ENV_DRAIN_TIMEOUT"]
