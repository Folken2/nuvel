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

from .cache_plugin import CachePlugin
from .console_logger_plugin import ConsoleLoggerPlugin
from .tool_events import ToolEventsPlugin
from .resilience_plugin import ResiliencePlugin
from .trace_plugin import TracePlugin

# ── Pre-configured instances (importable as dotted paths by ADK) ─────

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

# Ordered list of dotted paths for get_fast_api_app(extra_plugins=...)
PLUGIN_PATHS = [
    "meta_agent.plugins.trace",
    "meta_agent.plugins.context_filter",
    "meta_agent.plugins.console_logger",
    "meta_agent.plugins.tool_events",
    "meta_agent.plugins.resilience",
    "meta_agent.plugins.cache",
    "meta_agent.plugins.self_healing",
    "meta_agent.plugins.save_files",
]

__all__ = [
    "CachePlugin",
    "ConsoleLoggerPlugin",
    "ToolEventsPlugin",
    "ResiliencePlugin",
    "TracePlugin",
    "PLUGIN_PATHS",
]
