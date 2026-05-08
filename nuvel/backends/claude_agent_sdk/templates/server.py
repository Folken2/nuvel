"""
FastAPI server wrapping the Claude Agent SDK agent.

Endpoints:
    GET  /health           liveness check
    POST /run_sse/         server-sent events of the agent's response stream

Env:
    PORT                   default 8000
    API_KEY                if set, requires Bearer <API_KEY> on /run_sse/
    ANTHROPIC_API_KEY      required (read by the SDK directly)
"""

from __future__ import annotations

import json
import logging
import os
from importlib import import_module
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# Agent package name is stamped at scaffold time.
_AGENT_PKG = "{{agent_package}}"
_agent_mod = import_module(_AGENT_PKG)
get_client = _agent_mod.get_client

# Trace writer — best-effort; missing module shouldn't break the server.
try:
    write_trace = import_module(f"{_AGENT_PKG}.traces").write_trace
except Exception:
    def write_trace(_):
        pass

app = FastAPI(title="{{agent_name}}", description="{{agent_description}}")


def _serialize_block(block) -> dict:
    cls = block.__class__.__name__
    if cls == "TextBlock":
        return {"type": "text", "text": block.text}
    if cls == "ToolUseBlock":
        return {
            "type": "tool_use",
            "name": block.name,
            "input": block.input,
            "id": getattr(block, "id", None),
        }
    if cls == "ToolResultBlock":
        return {
            "type": "tool_result",
            "tool_use_id": getattr(block, "tool_use_id", None),
            "content": getattr(block, "content", None),
            "is_error": getattr(block, "is_error", False),
        }
    return {"type": cls.lower()}


def _serialize_message(msg) -> dict:
    cls = msg.__class__.__name__
    if cls == "AssistantMessage":
        return {
            "type": "assistant",
            "content": [_serialize_block(b) for b in msg.content],
        }
    if cls == "ResultMessage":
        return {
            "type": "result",
            "session_id": getattr(msg, "session_id", None),
            "total_cost_usd": getattr(msg, "total_cost_usd", None),
            "duration_ms": getattr(msg, "duration_ms", None),
            "num_turns": getattr(msg, "num_turns", None),
        }
    if cls == "UserMessage":
        return {"type": "user"}
    return {"type": cls.lower()}


async def _sse_stream(prompt: str) -> AsyncIterator[bytes]:
    async with get_client() as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            payload = _serialize_message(msg)
            if payload["type"] == "result":
                write_trace(msg)
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


def _check_auth(authorization: str | None) -> None:
    api_key = os.getenv("API_KEY")
    if not api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    if authorization.removeprefix("Bearer ").strip() != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "{{agent_name}}"}


@app.post("/run_sse/")
async def run_sse(
    request: Request,
    authorization: str | None = Header(default=None),
):
    _check_auth(authorization)
    body = await request.json()
    prompt = body.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="`prompt` is required")
    return StreamingResponse(_sse_stream(prompt), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
