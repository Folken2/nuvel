"""The model + tool loop behind every {{agent_name}} entrypoint.

The ACP server, the terminal CLI, and the Buzz relay worker all drive
:class:`AgentRuntime`. It owns three things:

* **sessions** — a message history per session id, in the OpenAI wire shape;
* **the turn loop** — call the model, run any tools it asked for, call again,
  until the model answers without tools (bounded by
  ``BUZZ_AGENT_MAX_TOOL_ITERATIONS``);
* **translation** — stream deltas out as transport-neutral
  :class:`AgentUpdate`\\ s so the callers stay thin and behave identically.

Transport is HTTP against an OpenAI-compatible ``/chat/completions``
endpoint (OpenRouter by default) — see ``agent.BuzzConfig``. ``httpx`` is
imported lazily so the protocol handshake works even in a bare environment.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..agent import Agent, Tool, build_agent

logger = logging.getLogger(__name__)

APP_NAME = "{{agent_name}}"


@dataclass
class AgentUpdate:
    """A single transport-agnostic event emitted while a turn runs."""

    kind: str  # "text" | "thought" | "tool_call" | "tool_result"
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: Any = None


def jsonable(value: Any) -> Any:
    """Best-effort coercion of a tool result into JSON-serializable data."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def to_message_content(prompt: "str | list[dict]") -> Any:
    """Map a text string or neutral prompt parts into OpenAI message content.

    Neutral parts come from the ACP server: ``{"kind": "text", "text": str}``
    or ``{"kind": "image", "mime_type": str, "data": bytes}``. Plain strings
    stay plain strings — some endpoints are picky about content arrays on
    text-only turns.
    """
    import base64

    if isinstance(prompt, str):
        return prompt

    content: list[dict] = []
    for part in prompt or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind")
        if kind == "text" and part.get("text"):
            content.append({"type": "text", "text": part["text"]})
        elif kind == "image" and part.get("data") is not None:
            mime = part.get("mime_type") or "image/png"
            b64 = base64.b64encode(part["data"]).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )

    if not content:
        return ""
    if len(content) == 1 and content[0]["type"] == "text":
        return content[0]["text"]
    return content


def _merge_tool_call_deltas(acc: dict[int, dict], deltas: list) -> None:
    """Fold streamed ``tool_calls`` deltas into accumulator ``acc``.

    Providers send a tool call in pieces: the first delta carries the id and
    name, later ones append argument-JSON fragments, all keyed by ``index``.
    """
    for delta in deltas or []:
        if not isinstance(delta, dict):
            continue
        index = delta.get("index", 0)
        entry = acc.setdefault(
            index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        )
        if delta.get("id"):
            entry["id"] = delta["id"]
        fn = delta.get("function") or {}
        if fn.get("name"):
            entry["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            entry["function"]["arguments"] += fn["arguments"]


def _decode_args(raw: str) -> dict:
    """Decode a tool call's argument JSON; ``{}`` when it isn't usable."""
    if not raw:
        return {}
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Could not decode tool arguments: %r", raw)
        return {}
    return args if isinstance(args, dict) else {"value": args}


class AgentRuntime:
    """Owns the agent, its sessions, and the turn loop.

    Construct once and reuse across turns and sessions. A session that
    supplies *extra tools* gets its own :class:`~{{agent_package}}.agent.Agent`
    with those tools appended; every other session shares the default one.
    """

    def __init__(self) -> None:
        self.app_name = APP_NAME
        self._default_agent = build_agent()
        # session_id -> message history (OpenAI wire shape).
        self._sessions: dict[str, list[dict]] = {}
        # session_id -> a per-session Agent (only when the session added tools).
        self._agents: dict[str, Agent] = {}
        # session_id -> the extra tools to close on shutdown.
        self._session_tools: dict[str, list[Tool]] = {}
        self._http: Any = None

    @property
    def agent(self) -> Agent:
        return self._default_agent

    def _agent_for(self, session_id: str) -> Agent:
        return self._agents.get(session_id, self._default_agent)

    # ── sessions ─────────────────────────────────────────────────────

    async def ensure_session(
        self,
        user_id: str,
        session_id: str,
        *,
        extra_tools: list[Tool] | None = None,
    ) -> None:
        """Create the session if new; wire its extra tools once.

        Later calls (e.g. the defensive re-ensure before a prompt) leave an
        existing session and its agent alone.
        """
        if extra_tools and session_id not in self._agents:
            try:
                self._agents[session_id] = build_agent(extra_tools=extra_tools)
                self._session_tools[session_id] = list(extra_tools)
            except Exception as exc:  # noqa: BLE001 — degrade to the default agent
                logger.warning(
                    "Could not build a per-session agent for %s (%s); "
                    "falling back to the default agent.",
                    session_id,
                    exc,
                )

        if session_id not in self._sessions:
            agent = self._agent_for(session_id)
            self._sessions[session_id] = [
                {"role": "system", "content": agent.config.instruction}
            ]

    def history(self, session_id: str) -> list[dict]:
        return self._sessions.get(session_id, [])

    # ── the turn loop ────────────────────────────────────────────────

    async def run_turn(
        self, user_id: str, session_id: str, prompt: "str | list[dict]"
    ) -> AsyncIterator[AgentUpdate]:
        """Run one prompt turn, yielding updates as the agent produces them.

        ``prompt`` is either plain text (the CLI path) or a list of neutral
        prompt parts from the ACP server; see :func:`to_message_content`.
        """
        await self.ensure_session(user_id, session_id)
        agent = self._agent_for(session_id)
        messages = self._sessions[session_id]
        messages.append({"role": "user", "content": to_message_content(prompt)})

        tools = agent.tool_map()
        for _ in range(max(1, agent.config.max_tool_iterations)):
            assistant: dict | None = None
            async for kind, payload in self._stream_once(agent, messages):
                if kind == "update":
                    yield payload
                else:
                    assistant = payload

            if assistant is None:  # stream produced nothing usable
                return
            messages.append(assistant)

            calls = assistant.get("tool_calls") or []
            if not calls:
                return

            for call in calls:
                async for update in self._run_tool(tools, call, messages):
                    yield update

        yield AgentUpdate(
            kind="text",
            text=(
                "\n[stopped: reached the tool-call limit "
                f"({agent.config.max_tool_iterations}) without a final answer]"
            ),
        )

    async def _run_tool(
        self, tools: dict[str, Tool], call: dict, messages: list[dict]
    ) -> AsyncIterator[AgentUpdate]:
        """Execute one tool call, appending its result message to ``messages``."""
        name = (call.get("function") or {}).get("name", "")
        call_id = call.get("id") or name
        args = _decode_args((call.get("function") or {}).get("arguments", ""))

        yield AgentUpdate(
            kind="tool_call", tool_call_id=call_id, tool_name=name, tool_args=args
        )

        tool = tools.get(name)
        if tool is None:
            result: Any = {"status": "error", "message": f"Unknown tool: {name}"}
        else:
            try:
                result = tool.handler(**args)
                if inspect.isawaitable(result):
                    result = await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — report back to the model
                logger.exception("Tool %s failed", name)
                result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}

        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": json.dumps(jsonable(result), ensure_ascii=False),
            }
        )
        yield AgentUpdate(
            kind="tool_result",
            tool_call_id=call_id,
            tool_name=name,
            tool_result=result,
        )

    # ── provider transport ───────────────────────────────────────────

    def _client(self):
        """Lazily build the shared HTTP client (keeps the import cost late)."""
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(
                timeout=self._default_agent.config.request_timeout
            )
        return self._http

    def _request(self, agent: Agent, messages: list[dict]) -> tuple[str, dict, dict]:
        cfg = agent.config
        problems = cfg.validate()
        if problems:
            raise RuntimeError("; ".join(problems))

        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        if cfg.provider == "openrouter":
            headers["X-Title"] = agent.name

        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "stream": True,
        }
        if agent.tools:
            body["tools"] = [t.to_openai_schema() for t in agent.tools]

        return f"{cfg.base_url}/chat/completions", headers, body

    async def _stream_once(
        self, agent: Agent, messages: list[dict]
    ) -> AsyncIterator[tuple[str, Any]]:
        """One model call. Yields ``("update", …)`` then ``("assistant", msg)``.

        ``msg`` is the assembled assistant message in wire shape, ready to
        append to the history — including any ``tool_calls`` the model made.
        """
        url, headers, body = self._request(agent, messages)

        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}

        async with self._client().stream(
            "POST", url, headers=headers, json=body
        ) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "replace")
                raise RuntimeError(
                    f"{agent.config.provider} returned {response.status_code}: {detail[:500]}"
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                # OpenRouter and friends surface chain-of-thought separately.
                reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                if reasoning:
                    yield ("update", AgentUpdate(kind="thought", text=reasoning))

                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield ("update", AgentUpdate(kind="text", text=content))

                if delta.get("tool_calls"):
                    _merge_tool_call_deltas(tool_calls, delta["tool_calls"])

        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts),
        }
        if tool_calls:
            assistant["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
        yield ("assistant", assistant)

    # ── shutdown ─────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the HTTP client and any closable per-session tools."""
        for tools in self._session_tools.values():
            for tool in tools:
                close = getattr(tool, "close", None)
                if close is None:
                    continue
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                    logger.warning("Error closing session tool %r: %s", tool, exc)
        self._session_tools.clear()
        self._agents.clear()

        if self._http is not None:
            await self._http.aclose()
            self._http = None
