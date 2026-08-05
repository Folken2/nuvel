"""Bridge the agent's file reads/writes through the editor's filesystem.

ACP defines two client-side methods — ``fs/read_text_file`` and
``fs/write_text_file`` — that let an agent see and edit files through the
*editor's* view, including unsaved changes in open buffers. That is the whole
point of ACP for coding workflows: the agent operates on what the user is
actually looking at, not a stale copy on disk.

A client advertises these in ``initialize`` under ``clientCapabilities.fs``.
When present, :class:`FsBridge` exposes ADK ``FunctionTool``s that issue
agent→client JSON-RPC requests (via the requester passed in) and await the
editor's response. The requester is :meth:`ACPAgent.request`.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Sends an agent→client request and resolves with the client's result.
ClientRequester = Callable[[str, dict], Awaitable[Any]]


class FsBridge:
    """Exposes the editor's filesystem to the agent as ADK tools.

    Bound to a single ACP session: every request carries this ``session_id``
    so the client can resolve paths against the right workspace.
    """

    def __init__(
        self,
        session_id: str,
        requester: ClientRequester,
        *,
        can_read: bool = True,
        can_write: bool = True,
    ) -> None:
        self._session_id = session_id
        self._request = requester
        self.can_read = can_read
        self.can_write = can_write

    async def read_text_file(self, path: str) -> str:
        """Read a text file from the user's editor workspace.

        Reflects the editor's live view — including unsaved edits in open
        buffers — so prefer this over any local disk read when working with
        the user's project files.

        Args:
            path: Absolute path to the file to read.

        Returns:
            The file's text content.
        """
        result = await self._request(
            "fs/read_text_file", {"sessionId": self._session_id, "path": path}
        )
        if isinstance(result, dict):
            return result.get("content", "") or ""
        return ""

    async def write_text_file(self, path: str, content: str) -> str:
        """Write a text file through the user's editor.

        Routes the write through the editor so it lands in the workspace the
        user sees (creating or overwriting the file). Prefer this over a local
        disk write when editing the user's project files.

        Args:
            path: Absolute path to the file to write.
            content: Full text content to write to the file.

        Returns:
            A short confirmation message.
        """
        await self._request(
            "fs/write_text_file",
            {"sessionId": self._session_id, "path": path, "content": content},
        )
        return f"Wrote {len(content)} characters to {path}."

    def function_tools(self) -> list:
        """Return the ADK ``FunctionTool``s the client's capabilities allow."""
        try:
            from google.adk.tools.function_tool import FunctionTool
        except ImportError as exc:
            logger.warning("ADK FunctionTool unavailable (%s); fs bridge disabled.", exc)
            return []

        tools = []
        if self.can_read:
            tools.append(FunctionTool(self.read_text_file))
        if self.can_write:
            tools.append(FunctionTool(self.write_text_file))
        return tools
