"""
FastAPI server for {{agent_name}}.

Endpoints:
    GET  /health           liveness check
    POST /run_sse/         server-sent events of the agent's response stream

Body for /run_sse/: {"prompt": "..."}

Env:
    PORT                   default 8000
    API_KEY                if set, requires Bearer <API_KEY> on /run_sse/
    ANTHROPIC_API_KEY      required (for the SDK)
    MANAGED_AGENT_ID       required (set by setup.py)
    MANAGED_AGENT_ENV_ID   required (set by setup.py)
"""

from __future__ import annotations

import json
import logging
import os
from importlib import import_module
from typing import AsyncIterator

import anyio
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

_AGENT_PKG = "{{agent_package}}"
run_session = import_module(_AGENT_PKG).run_session

app = FastAPI(title="{{agent_name}}", description="{{agent_description}}")

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def _required_env(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise HTTPException(status_code=500, detail=f"{key} is not set. Run setup.py.")
    return value


def _check_auth(authorization: str | None) -> None:
    api_key = os.getenv("API_KEY")
    if not api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    if authorization.removeprefix("Bearer ").strip() != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def _sse_stream(prompt: str) -> AsyncIterator[bytes]:
    client = _get_client()
    agent_id = _required_env("MANAGED_AGENT_ID")
    env_id = _required_env("MANAGED_AGENT_ENV_ID")

    # The SDK's session/event APIs are sync; bridge to async via anyio.
    def _run_sync_iter():
        for payload in run_session(client, agent_id, env_id, prompt):
            yield payload

    iterator = await anyio.to_thread.run_sync(lambda: iter(_run_sync_iter()))

    while True:
        try:
            payload = await anyio.to_thread.run_sync(next, iterator, _SENTINEL)
        except StopIteration:
            break
        if payload is _SENTINEL:
            break
        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
    yield b"data: [DONE]\n\n"


_SENTINEL = object()


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
