"""
Context budget plugin — keeps stale heavy payloads out of model requests.

ADK replays the full session history into every model request. Tool
responses persist in that history forever, so each read_attachment
chunk, Outlook snapshot, or Composio result blob would otherwise be
re-sent on every subsequent turn for the life of the session.

This plugin rewrites the OUTGOING ``llm_request.contents`` only — the
stored session is never mutated, so nothing is lost. Stale heavy tool
responses are replaced with a small stub telling the model to re-call
the tool if it still needs the data (for Outlook snapshots a re-call is
strictly better: it returns fresh state). The most recent results are
always kept intact so the model can use what it just asked for.

Eviction rules (chronological, oldest first):
  - responses from HEAVY_TOOLS larger than ``heavy_min_chars``
  - responses from ANY tool larger than ``any_tool_min_chars``
    (catches Composio search/message blobs without naming them)
  - keep the last ``keep_recent`` heavy results untouched
  - never touch the last ``protect_tail`` content entries (this is
    where load_artifacts appends file bytes for the current call)
  - stray inline binary blobs outside the protected tail are replaced
    with a text marker

Env overrides: CONTEXT_HEAVY_TOOLS (comma-separated, extends the set),
CONTEXT_KEEP_RECENT, CONTEXT_HEAVY_MIN_CHARS, CONTEXT_ANY_MIN_CHARS.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

logger = logging.getLogger(__name__)

DEFAULT_HEAVY_TOOLS: frozenset[str] = frozenset(
    {
        "read_attachment",
        "search_attachment",
        "get_selected_message",
        "get_current_compose",
        "get_full_outlook_state",
        "get_compose_draft_snapshot",
        "recall_memory",
    }
)


def _response_size(response) -> int:
    try:
        return len(json.dumps(response, default=str))
    except Exception:
        return len(str(response))


def evict_stale_payloads(
    contents: list,
    *,
    heavy_tools: set[str],
    keep_recent: int,
    heavy_min_chars: int,
    any_tool_min_chars: int,
    protect_tail: int,
) -> tuple[list, int]:
    """Pure eviction pass. Returns ``(new_contents, chars_saved)``.

    Builds new Content/Part objects for anything it rewrites — input
    objects (which may be shared with stored session events) are never
    mutated.
    """
    if len(contents) <= protect_tail:
        return contents, 0
    cutoff = len(contents) - protect_tail

    candidates: list[tuple[int, int, int]] = []  # (content_idx, part_idx, size)
    for ci in range(cutoff):
        for pi, part in enumerate(contents[ci].parts or []):
            fr = getattr(part, "function_response", None)
            if fr is None or not getattr(fr, "name", None):
                continue
            size = _response_size(fr.response)
            if (fr.name in heavy_tools and size >= heavy_min_chars) or (
                size >= any_tool_min_chars
            ):
                candidates.append((ci, pi, size))

    evict = candidates[:-keep_recent] if keep_recent > 0 else candidates
    saved = 0
    new_contents = list(contents)

    for ci, pi, size in evict:
        content = new_contents[ci]
        parts = list(content.parts)
        fr = parts[pi].function_response
        stub = {
            "status": "elided",
            "note": (
                f"Old {fr.name} output ({size} chars) was removed to keep "
                f"your context small. Call {fr.name} again if you still "
                "need this data."
            ),
        }
        parts[pi] = types.Part(
            function_response=types.FunctionResponse(
                id=getattr(fr, "id", None), name=fr.name, response=stub
            )
        )
        new_contents[ci] = content.model_copy(update={"parts": parts})
        saved += size

    # Stray inline binary outside the protected tail (e.g. a file part
    # that survived in history) — replace with a marker.
    for ci in range(cutoff):
        content = new_contents[ci]
        if not any(getattr(p, "inline_data", None) for p in (content.parts or [])):
            continue
        parts = []
        for p in content.parts:
            blob = getattr(p, "inline_data", None)
            if blob is not None and getattr(blob, "data", None):
                saved += len(blob.data)
                parts.append(
                    types.Part(
                        text=f"[file content ({blob.mime_type}) elided — "
                        "use load_artifacts to view it again]"
                    )
                )
            else:
                parts.append(p)
        new_contents[ci] = content.model_copy(update={"parts": parts})

    return new_contents, saved


class ContextBudgetPlugin(BasePlugin):
    def __init__(
        self,
        heavy_tools: set[str] | None = None,
        keep_recent: int | None = None,
        heavy_min_chars: int | None = None,
        any_tool_min_chars: int | None = None,
        protect_tail: int = 2,
    ) -> None:
        super().__init__(name="context_budget")
        extra = {
            t.strip()
            for t in os.getenv("CONTEXT_HEAVY_TOOLS", "").split(",")
            if t.strip()
        }
        base = set(heavy_tools) if heavy_tools is not None else set(DEFAULT_HEAVY_TOOLS)
        self.heavy_tools = base | extra
        self.keep_recent = (
            keep_recent
            if keep_recent is not None
            else int(os.getenv("CONTEXT_KEEP_RECENT", "2"))
        )
        self.heavy_min_chars = (
            heavy_min_chars
            if heavy_min_chars is not None
            else int(os.getenv("CONTEXT_HEAVY_MIN_CHARS", "1200"))
        )
        self.any_tool_min_chars = (
            any_tool_min_chars
            if any_tool_min_chars is not None
            else int(os.getenv("CONTEXT_ANY_MIN_CHARS", "8000"))
        )
        self.protect_tail = protect_tail

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> Optional[LlmResponse]:
        del callback_context
        contents = llm_request.contents or []
        new_contents, saved = evict_stale_payloads(
            contents,
            heavy_tools=self.heavy_tools,
            keep_recent=self.keep_recent,
            heavy_min_chars=self.heavy_min_chars,
            any_tool_min_chars=self.any_tool_min_chars,
            protect_tail=self.protect_tail,
        )
        if saved:
            llm_request.contents = new_contents
            logger.info("context_budget: elided ~%d chars from model request", saved)
        return None
