"""
Plugins for the Meta Agent.

Uses Google ADK's plugin system (BasePlugin) for cross-cutting concerns:
caching, resilience, and error recovery.

Pre-configured instances are exposed as module-level variables so that
``get_fast_api_app(extra_plugins=[...])`` can load them via dotted paths.
ADK's plugin loader checks ``isinstance(obj, BasePlugin)`` and uses
instances directly without re-instantiating.
"""

import os

from google.adk.plugins.context_filter_plugin import ContextFilterPlugin
from google.adk.plugins.reflect_retry_tool_plugin import (
    ReflectAndRetryToolPlugin,
    TrackingScope,
)
from google.adk.plugins.save_files_as_artifacts_plugin import (
    SaveFilesAsArtifactsPlugin,
)
from google.adk.cli.plugins.recordings_plugin import RecordingsPlugin
from google.adk.cli.plugins.replay_plugin import ReplayPlugin

from .cache_plugin import CachePlugin
from .console_logger_plugin import ConsoleLoggerPlugin
from .tool_events import ToolEventsPlugin
from .resilience_plugin import ResiliencePlugin
from .cost_guard_plugin import CostGuardPlugin
from .trace_plugin import TracePlugin

# ── Pre-configured instances (importable as dotted paths by ADK) ─────

cost_guard = CostGuardPlugin()
trace = TracePlugin()
context_filter = ContextFilterPlugin(
    num_invocations_to_keep=int(os.getenv("CONTEXT_FILTER_KEEP", "10")),
)
console_logger = ConsoleLoggerPlugin()
tool_events = ToolEventsPlugin()
resilience = ResiliencePlugin()
cache = CachePlugin()
self_healing = ReflectAndRetryToolPlugin(
    name="self_healing",
    max_retries=3,
    throw_exception_if_retry_exceeded=False,
    tracking_scope=TrackingScope.INVOCATION,
)
save_files = SaveFilesAsArtifactsPlugin()
recordings = RecordingsPlugin()
replay = ReplayPlugin()

# Ordered list of dotted paths for get_fast_api_app(extra_plugins=...)
PLUGIN_PATHS = [
    "nuvel.plugins.cost_guard",
    "nuvel.plugins.trace",
    "nuvel.plugins.context_filter",
    "nuvel.plugins.console_logger",
    "nuvel.plugins.tool_events",
    "nuvel.plugins.resilience",
    "nuvel.plugins.cache",
    "nuvel.plugins.self_healing",
    "nuvel.plugins.save_files",
    "nuvel.plugins.recordings",
    "nuvel.plugins.replay",
]

__all__ = [
    "CachePlugin",
    "ConsoleLoggerPlugin",
    "ToolEventsPlugin",
    "ResiliencePlugin",
    "TracePlugin",
    "PLUGIN_PATHS",
]
