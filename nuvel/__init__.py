"""nuvel — production-ready agents, your way."""


def __getattr__(name):
    # Expose ``nuvel.agent`` lazily (PEP 562) so importing the package for a
    # dependency-light path — e.g. ``nuvel mcp serve`` — doesn't pull the ADK
    # meta-agent and its heavy imports (dotenv, google-adk, …).
    if name == "agent":
        from . import agent as _agent

        return _agent
    # Lazy too: it needs pyyaml, and `nuvel doctor` must start without it.
    if name == "bots":
        from . import bots as _bots

        return _bots
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
