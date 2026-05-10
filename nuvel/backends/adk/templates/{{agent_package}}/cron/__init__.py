"""Cron scheduling for {{agent_name}}.

Hermes-Agent-inspired scheduled prompts. Off by default — set
``NUVEL_CRON_ENABLED=1`` to start the background tick loop.

Modules:
  schedule  — schedule-string parsing (relative, interval, cron, ISO).
  storage   — atomic JSON-backed job store under ``$NUVEL_CRON_DIR``.
  delivery  — wrap final response, route to local/origin/slack/telegram.
  scheduler — 60s tick loop with file lock and in-flight de-dup.
  service   — in-process facade used by tool, slash command, and HTTP API.
  routes    — FastAPI APIRouter mounted under ``/cron``.
  tools     — ``cronjob`` ADK tool.
"""

from .service import get_service, CronService, NUVEL_CRON_RUNNING_ENV

__all__ = ["get_service", "CronService", "NUVEL_CRON_RUNNING_ENV"]
