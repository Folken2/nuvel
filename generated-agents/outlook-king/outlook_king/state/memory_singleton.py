"""Module-level holder for the live memory service instance.

The backend sets this in its FastAPI lifespan; tools read it via
``get_memory_service()``. Module-level holders are intentionally simple
— ADK does not pass arbitrary services into tools, and the alternatives
(thread locals, context vars) are heavier without payoff here.

Production uses ``NeonMemoryService`` (Postgres); DEV_MODE without a
DATABASE_URL falls back to ``InMemoryMemoryService`` (resets on restart).
"""
from __future__ import annotations

from typing import Optional, Union

from .memory_service import NeonMemoryService
from .memory_service_dev import InMemoryMemoryService

MemoryService = Union[NeonMemoryService, InMemoryMemoryService]

_service: Optional[MemoryService] = None


def set_memory_service(service: MemoryService) -> None:
    global _service
    _service = service


def get_memory_service() -> MemoryService:
    if _service is None:
        raise RuntimeError(
            "Memory service not initialized. The FastAPI lifespan in "
            "backend/main.py must call set_memory_service() at startup."
        )
    return _service
