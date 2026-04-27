"""
Structured JSON logging configuration.

When LOG_FORMAT=json (default in production), outputs structured JSON logs
compatible with Datadog, Grafana Loki, CloudWatch, etc.

When LOG_FORMAT=text (default in dev), outputs human-readable colored logs.
"""

import logging
import os
import sys
import json
import time
import uuid
from contextvars import ContextVar
from typing import Any

# Context variable for request tracing
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # "json" or "text"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.") + f"{record.msecs:03.0f}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add request_id if available
        rid = request_id_var.get("")
        if rid:
            log_entry["request_id"] = rid

        # Add exception info
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }

        # Add any extra fields
        for key in ("tool", "duration_ms", "status", "session_id"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, default=str)


def setup_logging() -> None:
    """Configure logging based on LOG_FORMAT env var. Call once at startup."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)

    if LOG_FORMAT == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        ))

    root.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)


def generate_request_id() -> str:
    """Generate a short request ID for tracing."""
    return uuid.uuid4().hex[:12]
