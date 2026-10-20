"""ACP v2 protocol types, enums, and helpers.

The Agent Client Protocol (https://agentclientprotocol.com) specifies
JSON-RPC 2.0 over NDJSON (newline-delimited JSON) for agent–client
communication.  ACP v2 extends the original ACP v1 with session
management, cancellation, and structured session updates.

This module defines the Python types that mirror the v2 wire format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any

# ── Protocol version ──────────────────────────────────────────────────

ProtocolVersion = 2

# ── JSON-RPC message types ────────────────────────────────────────────


class JsonRpcMessageType(Enum):
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    ERROR = "error"


@dataclass
class JsonRpcRequest:
    """A JSON-RPC 2.0 request (client→agent or agent→client)."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None  # None for notifications
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d


@dataclass
class JsonRpcResponse:
    """A successful JSON-RPC 2.0 response."""

    id: str | int
    result: Any
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {"jsonrpc": self.jsonrpc, "id": self.id, "result": self.result}


@dataclass
class JsonRpcNotification:
    """A JSON-RPC 2.0 notification (no ``id``, no response expected)."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            d["params"] = self.params
        return d


@dataclass
class JsonRpcError:
    """A JSON-RPC 2.0 error response."""

    id: str | int | None
    code: int
    message: str
    data: Any = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return {"jsonrpc": self.jsonrpc, "id": self.id, "error": err}


# ── ACP method names ──────────────────────────────────────────────────


class AcpMethod(Enum):
    """ACP v2 method names (JSON-RPC ``method`` field)."""

    INITIALIZE = "initialize"
    SESSION_NEW = "session/new"
    SESSION_RESUME = "session/resume"
    SESSION_CLOSE = "session/close"
    SESSION_DELETE = "session/delete"
    SESSION_PROMPT = "session/prompt"
    SESSION_CANCEL = "session/cancel"
    SESSION_LIST = "session/list"


# ── Session state ─────────────────────────────────────────────────────


class SessionState(Enum):
    """The lifecycle state of an ACP session."""

    RUNNING = "running"
    IDLE = "idle"
    REQUIRES_ACTION = "requires_action"


# ── Stop reason ───────────────────────────────────────────────────────


class StopReason(Enum):
    """Why the agent stopped processing a turn."""

    END_TURN = "end_turn"
    CANCELLED = "cancelled"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    TOOL_USE = "tool_use"
    ERROR = "error"


# ── Session update variants ───────────────────────────────────────────


class SessionUpdateKind(Enum):
    """The ``sessionUpdate`` discriminator in a ``session/update`` notification."""

    USER_MESSAGE = "user_message"
    AGENT_MESSAGE = "agent_message"
    AGENT_THOUGHT = "agent_thought"
    AGENT_MESSAGE_CHUNK = "agent_message_chunk"
    AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
    TOOL_CALL = "tool_call"
    TOOL_CALL_UPDATE = "tool_call_update"
    STATE_UPDATE = "state_update"
    USAGE_UPDATE = "usage_update"


@dataclass
class UserMessage:
    """A user message added to the conversation."""

    session_update: str = "user_message"
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentMessage:
    """A complete agent message."""

    session_update: str = "agent_message"
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentThought:
    """Complete reasoning/thinking content from the agent."""

    session_update: str = "agent_thought"
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallUpdate:
    """An update to a tool call's status or output."""

    session_update: str = "tool_call_update"
    tool_call_id: str = ""
    status: str = ""  # "in_progress" | "completed" | "error"
    raw_output: Any = None
    raw_input: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateUpdate:
    """A session state transition."""

    session_update: str = "state_update"
    state: str = ""  # SessionState value


@dataclass
class UsageUpdate:
    """Token / cost accounting for the turn so far."""

    session_update: str = "usage_update"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AgentMessageChunk:
    """A streaming chunk of agent message text."""

    session_update: str = "agent_message_chunk"
    content: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentThoughtChunk:
    """A streaming chunk of agent reasoning/thinking."""

    session_update: str = "agent_thought_chunk"
    content: dict[str, Any] = field(default_factory=dict)


# Convenience union type for all update dataclasses.
SessionUpdate = (
    UserMessage
    | AgentMessage
    | AgentThought
    | ToolCallUpdate
    | StateUpdate
    | UsageUpdate
    | AgentMessageChunk
    | AgentThoughtChunk
)


# ── ACP error codes ───────────────────────────────────────────────────


class AcpError(IntEnum):
    """Standard ACP error codes (extend JSON-RPC's -32000..-32099 range)."""

    # Standard JSON-RPC codes.
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # ACP-specific codes.
    SESSION_NOT_FOUND = -32001
    SESSION_EXPIRED = -32002
    PROMPT_REJECTED = -32003
    UNKNOWN_CAPABILITY = -32004
    CANCEL_NOT_ALLOWED = -32005
    INVALID_AUTH = -32006
    RATE_LIMITED = -32007


# ── MCP server descriptor ─────────────────────────────────────────────


@dataclass
class McpServer:
    """Descriptor for an MCP (Model Context Protocol) server that the
    client wants the agent to connect to for the session.

    Mirrors the ``mcpServers`` array in ACP ``session/new`` params.
    """

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


# ── NDJSON helpers ────────────────────────────────────────────────────


def serialize_ndjson(obj: dict[str, Any]) -> str:
    """Serialize a dictionary to a single NDJSON line (JSON + newline).

    Args:
        obj: The dictionary to serialize.  Must be JSON-serializable.

    Returns:
        A JSON-encoded string followed by a ``\\n`` character, ready to
        write to the protocol transport.
    """
    return json.dumps(obj, ensure_ascii=False) + "\n"


def parse_ndjson(line: str) -> dict[str, Any]:
    """Parse a single NDJSON line into a dictionary.

    Args:
        line: A raw line from the transport (stripped of trailing
            whitespace by the caller).

    Returns:
        The decoded dictionary.

    Raises:
        json.JSONDecodeError: If the line is not valid JSON.
    """
    return json.loads(line)