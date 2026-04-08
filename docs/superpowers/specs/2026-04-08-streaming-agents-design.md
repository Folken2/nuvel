# Streaming Voice/Video Agents — Design Spec

> **Date:** 2026-04-08
> **Status:** Approved
> **Goal:** Add bidirectional streaming (voice/video) support to generated agents via Gemini Live API, toggled by env var.

---

## Context

The meta-agent generates text-based agents. ADK supports real-time voice/video agents via the Gemini Live API using `LiveRequestQueue`, `Runner.run_live()`, and WebSocket transport. This feature adds streaming as a conditional capability in the existing template — no separate template tree.

Source: [ADK Streaming Dev Guide](https://google.github.io/adk-docs/streaming/dev-guide/part1.md)

---

## Architecture

```
Browser (mic/cam) → WebSocket /ws/{user_id}/{session_id}?token=XXX
    ↓
FastAPI (run_adk.py)
    ↓
streaming.py:
    upstream_task:   WebSocket → LiveRequestQueue (text via send_content, audio via send_realtime)
    downstream_task: Runner.run_live() → WebSocket (events as JSON)
    ↓
Gemini Live API (BIDI mode, VAD enabled by default)
```

When `STREAMING_ENABLED=false` (default), the template works exactly as today with `get_fast_api_app()` and OpenRouter/LiteLLM.

When `STREAMING_ENABLED=true`, `run_adk.py` builds the FastAPI app manually: creates its own `Runner` + `SessionService`, mounts standard ADK routes, then adds the WebSocket endpoint via `streaming.py`.

---

## Design

### 1. New Template File: `{{agent_package}}/streaming.py`

Encapsulates all streaming logic:

- **`create_run_config()`** — builds `RunConfig` with:
  - `StreamingMode.BIDI`
  - `response_modalities=["AUDIO"]`
  - `input_audio_transcription` and `output_audio_transcription` enabled
  - VAD enabled by default (automatic activity detection)
  - `session_resumption` enabled

- **`websocket_handler(websocket, user_id, session_id, runner, session_service)`**:
  - Creates `LiveRequestQueue` per session
  - `upstream_task`: receives JSON from client, dispatches text (`send_content`) or audio (`send_realtime`) to queue
  - `downstream_task`: consumes `runner.run_live()`, sends events as JSON to client
  - Runs both via `asyncio.gather()`
  - Closes queue in `finally` block

- **`mount_streaming(app, runner, session_service)`** — registers `/ws/{user_id}/{session_id}` WebSocket route. Called from `run_adk.py` only when streaming is enabled.

- **WebSocket auth:** Simple token query param (`?token=`) validated against `API_KEY` env var.

### 2. New Template File: `static/test_client.html`

Single HTML file with inline JS, no dependencies:

- Connect/disconnect button with WebSocket URL input
- Mic access via `getUserMedia()` + `AudioWorklet`/`ScriptProcessorNode` for PCM capture (16kHz, 16-bit, mono)
- Sends audio as base64 PCM chunks over WebSocket
- Receives and plays audio responses via `AudioContext` (24kHz)
- Displays text transcriptions (input + output) in a chat log
- Token auth via query param
- Connection status indicator
- No video capture (can be added later)
- No framework dependencies

### 3. Modified Template: `run_adk.py`

Conditional branch following the `DEV_MODE` pattern:

```python
streaming_enabled = os.getenv("STREAMING_ENABLED", "false").lower() in ("true", "1", "yes")

if streaming_enabled:
    # Build app manually: Runner + SessionService + WebSocket
    from {{agent_package}}.streaming import mount_streaming
    # ... create Runner with LIVE_MODEL, mount standard routes, mount WebSocket
    app.mount("/static", StaticFiles(directory="static"), name="static")
else:
    # Existing get_fast_api_app() path — unchanged
    ...
```

When streaming is enabled, `run_adk.py` creates its own `Runner` and `InMemorySessionService` (or DB-backed in production), gives full control over model selection and WebSocket mounting.

### 4. Modified Template: `config/llm.py`

Add:
```python
# Streaming model — Gemini directly (not via LiteLLM)
LIVE_MODEL = os.getenv("LIVE_MODEL", "gemini-2.0-flash-live-001")
```

Just a string. No `LiteLlm` wrapper. Only used when `STREAMING_ENABLED=true`.

### 5. Modified Template: `.env.example`

New section:
```env
# ── Streaming / Voice Agent ─────────────────────────────────────────
# STREAMING_ENABLED=false
# GOOGLE_API_KEY=your_google_api_key_here
# LIVE_MODEL=gemini-2.0-flash-live-001
```

### 6. New Skill: `adk-streaming`

```
meta_agent/skills/adk-streaming/
  SKILL.md                          # L2: When/how to build streaming agents
  references/
    streaming-patterns.md           # L3: RunConfig options, VAD modes, audio formats
    live-api-reference.md           # L3: LiveRequestQueue API, event types, error handling
```

The meta-agent loads this skill when designing a streaming agent to customize `streaming.py` (voice config, speech language, VAD mode).

### 7. Modified: `meta_agent/prompt/instructions.py`

**Step 1 (Discovery):** Add voice/video question:
```
- **Voice/Video**: Does this agent need voice or live video capabilities?
  (Only when user mentions voice agents, live agents, or real-time audio/video)
```

**Step 2 (Design):** When user wants voice/video:
- Recommend `STREAMING_ENABLED=true`
- Note `GOOGLE_API_KEY` requirement (not OpenRouter)
- Load `adk-streaming` skill for streaming-specific design guidance

**Step 4 (Generate):** Add to skill loading list:
- `load_skill("adk-streaming")` before configuring streaming agents

---

## What Does NOT Change

- `scaffold.py` — no new flags; streaming is runtime config via `.env`
- `agent.py.tmpl` — agent definition unchanged; Runner handles model selection
- Existing plugins — work as before
- Non-streaming agents — zero impact; `STREAMING_ENABLED` defaults to `false`

---

## Summary

| Component | Change | Files |
|-----------|--------|-------|
| New template file | `streaming.py` | 1 new |
| New template file | `static/test_client.html` | 1 new |
| Modified template | `run_adk.py` | 1 edit |
| Modified template | `config/llm.py` | 1 edit |
| Modified template | `.env.example` | 1 edit |
| New skill | `adk-streaming/` + 2 L3 references | 3 new |
| Modified prompt | `instructions.py` | 1 edit |
| **Total** | | **5 new, 4 edits** |
