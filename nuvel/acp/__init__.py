"""ACP — Agent Client Protocol v2 for Nuvel.

Nuvel-generated agents can speak ACP over stdio, making them runnable as
Buzz-compatible "custom harnesses."  See ``README.md`` for the design
rationale and the directory layout.
"""

from .protocol import (
    AcpError,
    McpServer,
    ProtocolVersion,
    SessionState,
    StopReason,
    parse_ndjson,
    serialize_ndjson,
)
from .server import AcpServer
from .stdin_writer import StdinWriter, WriteRequest

__all__ = [
    "AcpError",
    "AcpServer",
    "McpServer",
    "ProtocolVersion",
    "SessionState",
    "StdinWriter",
    "StopReason",
    "WriteRequest",
    "parse_ndjson",
    "serialize_ndjson",
]