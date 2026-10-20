"""
ADK's record/replay plugins, off unless NUVEL_RECORD_REPLAY=1.

ADK's RecordingsPlugin and ReplayPlugin exist for `adk conformance` test
runs. They switch on from session state: ``_adk_recordings_config`` names a
directory the plugin creates and writes the full LLM requests into, and
``_adk_replay_config`` names one it loads recordings from. ADK's HTTP API lets
any caller set session state when creating or patching a session, so on a
server those keys are client input, and left alone they give every caller a
file write anywhere the process can write.

These subclasses drop both keys from the in-flight session at the start of
every run unless NUVEL_RECORD_REPLAY is set, which leaves the parent plugins
(and ADK's own replay model swap, which reads the same key) with nothing to
act on. Only enable the flag for local conformance work, never on a server
with untrusted callers.
"""

import logging
import os
from typing import Optional

from google.adk.agents.invocation_context import InvocationContext
from google.adk.cli.plugins.recordings_plugin import RecordingsPlugin
from google.adk.cli.plugins.replay_plugin import ReplayPlugin
from google.genai import types

logger = logging.getLogger(__name__)

ENV_ENABLED = "NUVEL_RECORD_REPLAY"

_CONFIG_KEYS = ("_adk_recordings_config", "_adk_replay_config")


def record_replay_enabled() -> bool:
    return os.getenv(ENV_ENABLED, "").lower() in ("1", "true", "yes")


def _strip_client_config(invocation_context: InvocationContext) -> None:
    session = getattr(invocation_context, "session", None)
    state = getattr(session, "state", None)
    if not isinstance(state, dict):
        return
    for key in _CONFIG_KEYS:
        if state.pop(key, None) is not None:
            logger.warning(
                "Ignored %s in session %s: record/replay is off (set %s=1 to enable)",
                key,
                getattr(session, "id", "?"),
                ENV_ENABLED,
            )


class GatedRecordingsPlugin(RecordingsPlugin):
    """RecordingsPlugin that only records when NUVEL_RECORD_REPLAY is set."""

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        if not record_replay_enabled():
            _strip_client_config(invocation_context)
            return None
        return await super().before_run_callback(invocation_context=invocation_context)


class GatedReplayPlugin(ReplayPlugin):
    """ReplayPlugin that only replays when NUVEL_RECORD_REPLAY is set."""

    async def before_run_callback(
        self, *, invocation_context: InvocationContext
    ) -> Optional[types.Content]:
        if not record_replay_enabled():
            _strip_client_config(invocation_context)
            return None
        return await super().before_run_callback(invocation_context=invocation_context)
