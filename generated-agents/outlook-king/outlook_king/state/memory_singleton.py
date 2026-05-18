"""Module-level holder for the live NeonMemoryService instance.

The backend sets this in its FastAPI lifespan; tools read it via
``get_memory_service()``. Module-level holders are intentionally simple
— ADK does not pass arbitrary services into tools, and the alternatives
(thread locals, context vars) are heavier without payoff here.
"""
from __future__ import annotations

from typing import Optional

from .memory_service import NeonMemoryService

_service: Optional[NeonMemoryService] = None


def set_memory_service(service: NeonMemoryService) -> None:
    global _service
    _service = service


def get_memory_service() -> NeonMemoryService:
    if _service is None:
        raise RuntimeError(
            "NeonMemoryService not initialized. The FastAPI lifespan in "
            "backend/main.py must call set_memory_service() at startup."
        )
    return _service
