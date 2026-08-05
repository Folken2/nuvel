"""In-process facade for cron CRUD + manual trigger.

This is the single entry point used by the HTTP routes, the slash command,
and the ADK ``cronjob`` tool. Keeping the logic here lets the tool call
the same code path the API uses (no HTTP round-trip from inside the agent).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from . import storage
from .schedule import compute_next_run, parse_schedule

logger = logging.getLogger(__name__)


# Recursion guard: when the scheduler invokes a cron job, it sets this env
# var so the ``cronjob`` tool refuses to mutate state during the run.
NUVEL_CRON_RUNNING_ENV = "NUVEL_CRON_RUNNING"

# HITL-gated creation: when enabled, new jobs land as ``pending`` and only
# start ticking once a human calls ``confirm_job``. Opt-in (default off) so
# existing generated agents keep firing jobs immediately on create; set
# ``NUVEL_CRON_HITL_CREATE=1`` to require confirmation, or ``0`` to disable.
ENV_HITL_CREATE = "NUVEL_CRON_HITL_CREATE"
_TRUTHY = {"1", "true", "yes", "on"}


def hitl_create_enabled() -> bool:
    return (os.environ.get(ENV_HITL_CREATE, "") or "").strip().lower() in _TRUTHY


_VALID_DELIVERIES_PREFIX = ("slack:", "telegram:")
_VALID_DELIVERIES_EXACT = {"local", "origin"}


def _validate_delivery(value: str) -> str:
    v = (value or "").strip()
    if v in _VALID_DELIVERIES_EXACT:
        return v
    if any(v.startswith(p) and len(v) > len(p) for p in _VALID_DELIVERIES_PREFIX):
        return v
    raise ValueError(
        f"Invalid delivery {value!r}. Use 'local', 'origin', "
        "'slack:<channel>', or 'telegram:<chat_id>'."
    )


def _clean_secrets(secrets: list[str] | None) -> list[str] | None:
    """Normalize a declared ``secrets`` list, or ``None`` when unset.

    ``None`` means the job did not declare a scope and (for back-compat) sees
    the full environment. A provided list is de-duplicated, order-preserving,
    and stripped of blanks — an explicit empty list stays empty (no secrets).
    """
    if secrets is None:
        return None
    seen: dict[str, None] = {}
    for name in secrets:
        if isinstance(name, str) and name.strip():
            seen.setdefault(name.strip(), None)
    return list(seen)


class CronService:
    """All public methods are synchronous (file I/O is fast for MVP scale)."""

    # ---- CRUD ---------------------------------------------------------

    def list_jobs(self) -> list[dict[str, Any]]:
        with storage.transaction():
            return list(storage.load_jobs())

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        for j in self.list_jobs():
            if j.get("id") == job_id:
                return j
        return None

    def create_job(
        self,
        *,
        name: str,
        prompt: str,
        schedule: str,
        delivery: str = "local",
        origin: dict[str, Any] | None = None,
        secrets: list[str] | None = None,
    ) -> dict[str, Any]:
        if not name or not name.strip():
            raise ValueError("name must not be empty")
        if not prompt or not prompt.strip():
            raise ValueError("prompt must not be empty")
        delivery = _validate_delivery(delivery)
        parsed = parse_schedule(schedule)

        now = datetime.now(timezone.utc)
        next_run = compute_next_run(parsed, now=now)
        if next_run is None:
            raise ValueError("schedule resolves to no future run")

        # HITL gate: a confirmation-required job lands as ``pending`` and does
        # not tick until ``confirm_job`` promotes it to ``active``.
        status = "pending" if hitl_create_enabled() else "active"

        job = {
            "id": storage.new_job_id(),
            "name": name.strip(),
            "prompt": prompt,
            "schedule": parsed.raw,
            "status": status,
            "delivery": delivery,
            "origin": origin or None,
            "secrets": _clean_secrets(secrets),
            "created_at": now.isoformat(),
            "next_run_at": next_run.isoformat(),
            "last_run_at": None,
            "last_run_output_path": None,
            "last_error": None,
        }
        with storage.transaction():
            jobs = storage.load_jobs()
            jobs.append(job)
            storage.save_jobs(jobs)
        return job

    def confirm_job(self, job_id: str) -> dict[str, Any]:
        """Promote a ``pending`` (HITL-gated) job to ``active`` so it can tick.

        Idempotent: confirming an already-active job returns it unchanged.
        """
        with storage.transaction():
            jobs = storage.load_jobs()
            for j in jobs:
                if j.get("id") == job_id:
                    if j.get("status") == "pending":
                        j["status"] = "active"
                        storage.save_jobs(jobs)
                    return j
        raise KeyError(job_id)

    def update_job(self, job_id: str, **fields: Any) -> dict[str, Any]:
        mutable = {"name", "prompt", "schedule", "delivery", "status"}
        unknown = set(fields) - mutable
        if unknown:
            raise ValueError(f"Cannot update fields: {sorted(unknown)}")

        with storage.transaction():
            jobs = storage.load_jobs()
            for j in jobs:
                if j.get("id") == job_id:
                    if "delivery" in fields:
                        fields["delivery"] = _validate_delivery(fields["delivery"])
                    if "schedule" in fields and fields["schedule"]:
                        parsed = parse_schedule(fields["schedule"])
                        # Recompute next_run_at from "now" anchor.
                        nxt = compute_next_run(
                            parsed, now=datetime.now(timezone.utc),
                        )
                        if nxt is None:
                            raise ValueError("schedule resolves to no future run")
                        j["schedule"] = parsed.raw
                        j["next_run_at"] = nxt.isoformat()
                        fields.pop("schedule")
                    if "status" in fields and fields["status"] not in (
                        "active", "paused", "completed",
                    ):
                        raise ValueError(f"Bad status: {fields['status']!r}")
                    j.update(fields)
                    storage.save_jobs(jobs)
                    return j
        raise KeyError(job_id)

    def delete_job(self, job_id: str) -> bool:
        with storage.transaction():
            jobs = storage.load_jobs()
            new = [j for j in jobs if j.get("id") != job_id]
            if len(new) == len(jobs):
                return False
            storage.save_jobs(new)
            return True

    # ---- State transitions -------------------------------------------

    def pause(self, job_id: str) -> dict[str, Any]:
        return self.update_job(job_id, status="paused")

    def resume(self, job_id: str) -> dict[str, Any]:
        return self.update_job(job_id, status="active")

    def trigger_now(self, job_id: str) -> dict[str, Any]:
        """Set next_run_at to now so the next tick picks up the job."""
        with storage.transaction():
            jobs = storage.load_jobs()
            for j in jobs:
                if j.get("id") == job_id:
                    j["next_run_at"] = datetime.now(timezone.utc).isoformat()
                    if j.get("status") == "completed":
                        j["status"] = "active"
                    storage.save_jobs(jobs)
                    return j
        raise KeyError(job_id)


_SINGLETON: CronService | None = None


def get_service() -> CronService:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = CronService()
    return _SINGLETON
