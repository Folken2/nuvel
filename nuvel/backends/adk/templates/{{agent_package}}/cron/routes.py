"""FastAPI routes for cron job CRUD.

Mounted under ``/cron`` on the agent's FastAPI app, behind the same
``API_KEY`` middleware that protects the rest of the surface.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .service import get_service

router = APIRouter(prefix="/cron", tags=["cron"])


class CreateJobRequest(BaseModel):
    name: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    schedule: str = Field(..., min_length=1)
    delivery: str = "local"
    origin: Optional[dict[str, Any]] = None
    # Declared env-var names the job may read. Enforced only when
    # NUVEL_CRON_SCOPE_SECRETS=1; None = full env (back-compat).
    secrets: Optional[list[str]] = None


class UpdateJobRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    schedule: Optional[str] = None
    delivery: Optional[str] = None
    status: Optional[str] = None


def _filter_none(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    return {"jobs": get_service().list_jobs()}


@router.post("/jobs")
async def create_job(body: CreateJobRequest) -> dict[str, Any]:
    try:
        return get_service().create_job(
            name=body.name, prompt=body.prompt, schedule=body.schedule,
            delivery=body.delivery, origin=body.origin, secrets=body.secrets,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = get_service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.patch("/jobs/{job_id}")
async def update_job(job_id: str, body: UpdateJobRequest) -> dict[str, Any]:
    fields = _filter_none(body.model_dump())
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        return get_service().update_job(job_id, **fields)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/jobs/{job_id}/confirm")
async def confirm_job(job_id: str) -> dict[str, Any]:
    """Promote an HITL-gated ``pending`` job to ``active``."""
    try:
        return get_service().confirm_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@router.post("/jobs/{job_id}/run")
async def trigger_job(job_id: str) -> dict[str, Any]:
    try:
        return get_service().trigger_now(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@router.post("/jobs/{job_id}/pause")
async def pause_job(job_id: str) -> dict[str, Any]:
    try:
        return get_service().pause(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str) -> dict[str, Any]:
    try:
        return get_service().resume(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="job not found")


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    if not get_service().delete_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"ok": True, "id": job_id}
