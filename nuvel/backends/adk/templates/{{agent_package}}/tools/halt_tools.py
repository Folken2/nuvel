"""Recovery tool for a latched halt.

The guardrails plugin latches a shared halt signal when it detects a runaway
loop (no progress, or a tool failing identically); while it is latched every
turn comes back as ``[halted: <reason>]``. This tool lets the agent lift that
latch and resume once it has seen — and can react to — the reason.
"""

from google.adk.tools import FunctionTool, ToolContext

from ..guardrails.halt_consumer import acknowledge_halt_tool


def acknowledge_halt(tool_context: ToolContext) -> dict:
    """Acknowledge a halted session and resume.

    Call this when you receive a ``[halted: ...]`` response. It clears the halt
    signal, resets the guard counters so the halt does not immediately re-trip,
    and reports the reason the session was halted so you can avoid repeating
    whatever triggered it.

    Returns:
        A status dict: ``status`` is ``acknowledged`` (with the halt ``reason``)
        or ``no_halt``, plus a human-readable ``message``.
    """
    return acknowledge_halt_tool(tool_context.state)


halt_tool_list = [FunctionTool(acknowledge_halt)]
