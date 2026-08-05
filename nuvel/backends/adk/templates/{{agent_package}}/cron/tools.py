"""ADK ``cronjob`` tool — single multiplexed tool over the cron service.

The agent calls this tool to manage its own scheduled jobs. State-mutating
actions are blocked when ``NUVEL_CRON_RUNNING=1`` (i.e. when the call
originates *from* a cron-spawned run) to prevent recursive scheduling.
"""

from __future__ import annotations

import os
from typing import Any, Literal, Optional

from google.adk.tools import FunctionTool

from .service import NUVEL_CRON_RUNNING_ENV, get_service


_MUTATING = {"create", "update", "pause", "resume", "run", "remove", "confirm"}


def _parse_secrets(raw: str) -> list[str] | None:
    """Parse the comma-separated ``secrets`` arg. ``""`` → None (full env)."""
    if not raw or not raw.strip():
        return None
    return [n.strip() for n in raw.split(",") if n.strip()]


def cronjob(
    action: Literal["create", "list", "get", "update", "pause", "resume", "run", "remove", "confirm"],
    job_id: str = "",
    name: str = "",
    prompt: str = "",
    schedule: str = "",
    delivery: str = "",
    secrets: str = "",
    new_name: str = "",
    new_prompt: str = "",
    new_schedule: str = "",
    new_status: str = "",
) -> dict:
    """Manage scheduled prompts (cron jobs).

    Use this tool to schedule the agent to run a prompt later — once or on a
    recurring schedule — and to inspect/pause/resume/remove existing jobs.

    Args:
        action: One of ``create``, ``list``, ``get``, ``update``, ``pause``,
                ``resume``, ``run``, ``remove``, ``confirm``.
        job_id: Required for get/update/pause/resume/run/remove/confirm.
        name: Job name (required for ``create``).
        prompt: Prompt to run when the job fires (required for ``create``).
        schedule: Schedule string (required for ``create``). Examples:
                  ``"30m"``, ``"every 1h"``, ``"0 9 * * *"``,
                  ``"2026-12-15T09:00:00"``.
        delivery: ``local`` (default), ``origin``, ``slack:<channel>``,
                  or ``telegram:<chat_id>``.
        secrets: Optional comma-separated env-var names the job may read
                 (e.g. ``"SLACK_TOKEN,GITHUB_TOKEN"``). Enforced only when
                 secret scoping is enabled; empty = full env (back-compat).
        new_name / new_prompt / new_schedule / new_status: For ``update``.

    Returns:
        A dict with ``status`` ("ok" or "error") and the relevant payload.
    """
    if action in _MUTATING and os.environ.get(NUVEL_CRON_RUNNING_ENV) == "1":
        return {
            "status": "error",
            "message": (
                "Cron jobs cannot create or modify cron jobs while running. "
                "Reply with the result you want delivered; the schedule is "
                "managed outside this run."
            ),
        }

    svc = get_service()
    try:
        if action == "list":
            return {"status": "ok", "jobs": svc.list_jobs()}
        if action == "get":
            if not job_id:
                return {"status": "error", "message": "job_id required"}
            job = svc.get_job(job_id)
            if job is None:
                return {"status": "error", "message": f"no job {job_id!r}"}
            return {"status": "ok", "job": job}
        if action == "create":
            return {
                "status": "ok",
                "job": svc.create_job(
                    name=name, prompt=prompt, schedule=schedule,
                    delivery=delivery or "local",
                    secrets=_parse_secrets(secrets),
                ),
            }
        if action == "confirm":
            if not job_id:
                return {"status": "error", "message": "job_id required"}
            return {"status": "ok", "job": svc.confirm_job(job_id)}
        if action == "update":
            fields: dict[str, Any] = {}
            if new_name:
                fields["name"] = new_name
            if new_prompt:
                fields["prompt"] = new_prompt
            if new_schedule:
                fields["schedule"] = new_schedule
            if new_status:
                fields["status"] = new_status
            if delivery:
                fields["delivery"] = delivery
            if not fields:
                return {"status": "error", "message": "no fields to update"}
            return {"status": "ok", "job": svc.update_job(job_id, **fields)}
        if action == "pause":
            return {"status": "ok", "job": svc.pause(job_id)}
        if action == "resume":
            return {"status": "ok", "job": svc.resume(job_id)}
        if action == "run":
            return {"status": "ok", "job": svc.trigger_now(job_id)}
        if action == "remove":
            if not svc.delete_job(job_id):
                return {"status": "error", "message": f"no job {job_id!r}"}
            return {"status": "ok", "removed": job_id}
        return {"status": "error", "message": f"unknown action {action!r}"}
    except KeyError as exc:
        return {"status": "error", "message": f"no job {exc!s}"}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}


cronjob_tool_list = [FunctionTool(cronjob)]
