"""Long-horizon safety + resilience guardrails.

Two families of protection for agents that run for a long time:

* **Halt guards** — detect runaway loops (no progress, a tool failing
  identically) and latch a shared halt signal that ``GuardrailsPlugin`` consumes
  to short-circuit the model. See ``halt_consumer``, ``no_progress``,
  ``repeated_failure``, ``guardrails_plugin``.
* **Command / exfiltration guards** — structurally classify shell commands for
  destructive operations and scan tool arguments for leaked secrets. See
  ``command_safety``, ``command_classify``, ``exfil_guard``.
"""

from __future__ import annotations

from .command_classify import (
    command_prefix,
    has_command_substitution,
    has_redirection,
    split_segments,
    strip_wrapper,
)
from .command_guard import command_guard_callback
from .command_safety import classify, lex, segments
from .exfil_guard import exfil_guard
from .guardrails_plugin import GuardrailsPlugin
from .halt_consumer import (
    HALT_HANDOFF_DELIVERED_STATE_KEY,
    HALT_REASON_STATE_KEY,
    acknowledge_halt,
    acknowledge_halt_tool,
    halt_consumer_callback,
    halt_content,
    latch_halt,
    reset_halt_handoff,
)
from .no_progress import NoProgressGuard
from .repeated_failure import LAST_ERROR_STATE_KEY, RepeatedFailureGuard

__all__ = [
    # halt latch
    "HALT_REASON_STATE_KEY",
    "HALT_HANDOFF_DELIVERED_STATE_KEY",
    "halt_content",
    "halt_consumer_callback",
    "latch_halt",
    "acknowledge_halt",
    "acknowledge_halt_tool",
    "reset_halt_handoff",
    # guards
    "NoProgressGuard",
    "RepeatedFailureGuard",
    "LAST_ERROR_STATE_KEY",
    "GuardrailsPlugin",
    # command parsing / safety
    "strip_wrapper",
    "split_segments",
    "command_prefix",
    "has_redirection",
    "has_command_substitution",
    "lex",
    "classify",
    "segments",
    "command_guard_callback",
    # exfiltration
    "exfil_guard",
]
