# Streaming Voice/Video Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bidirectional streaming (voice/video) support to generated agents via Gemini Live API, toggled by `STREAMING_ENABLED` env var.

**Architecture:** When `STREAMING_ENABLED=true`, `run_adk.py` builds the FastAPI app manually (instead of `get_fast_api_app()`), creates its own `Runner` + `SessionService`, mounts standard routes, then adds a WebSocket `/ws/{user_id}/{session_id}` endpoint powered by `streaming.py`. A `static/test_client.html` provides a minimal browser-based voice test UI.

**Tech Stack:** Google ADK (`LiveRequestQueue`, `Runner.run_live`, `RunConfig`, `StreamingMode`), FastAPI WebSocket, Gemini Live API, Web Audio API

**Spec:** `docs/superpowers/specs/2026-04-08-streaming-agents-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `meta_agent/templates/{{agent_package}}/streaming.py` | Create | WebSocket handler, LiveRequestQueue wiring, RunConfig builder |
| `meta_agent/templates/static/test_client.html` | Create | Minimal browser voice test client |
| `meta_agent/templates/run_adk.py` | Modify | Add streaming branch with manual Runner |
| `meta_agent/templates/{{agent_package}}/config/llm.py` | Modify | Add `LIVE_MODEL` config |
| `meta_agent/templates/.env.example` | Modify | Add streaming env vars |
| `meta_agent/skills/adk-streaming/SKILL.md` | Create | L2 streaming skill for meta-agent |
| `meta_agent/skills/adk-streaming/references/streaming-patterns.md` | Create | L3 RunConfig, VAD, audio formats |
| `meta_agent/skills/adk-streaming/references/live-api-reference.md` | Create | L3 LiveRequestQueue API, event types |
| `meta_agent/prompt/instructions.py` | Modify | Add voice/video to discovery + streaming skill to generate |
| `scaffold.py` | Modify | Add `.html` to TEXT_EXTENSIONS |

---

### Task 1: Add `LIVE_MODEL` to `config/llm.py`

**Files:**
- Modify: `meta_agent/templates/{{agent_package}}/config/llm.py:34-35`

- [ ] **Step 1: Add LIVE_MODEL config**

Append after line 34 (after `REASONING_MODEL`):

```python
# Streaming model — Gemini directly (not via LiteLLM).
# Only used when STREAMING_ENABLED=true.
LIVE_MODEL = os.getenv("LIVE_MODEL", "gemini-2.0-flash-live-001")
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('meta_agent/templates/{{agent_package}}/config/llm.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/\{\{agent_package\}\}/config/llm.py
git commit -m "feat(template): add LIVE_MODEL config for Gemini streaming"
```

---

### Task 2: Add streaming env vars to `.env.example`

**Files:**
- Modify: `meta_agent/templates/.env.example:84`

- [ ] **Step 1: Append streaming section**

Add at end of file:

```env

# ── Streaming / Voice Agent ─────────────────────────────────────────

# Optional: Enable bidirectional streaming (voice/video) via Gemini Live API
# Requires GOOGLE_API_KEY. Uses WebSocket at /ws/{user_id}/{session_id}
# STREAMING_ENABLED=false

# Required when STREAMING_ENABLED=true: Google API key for Gemini Live API
# GOOGLE_API_KEY=your_google_api_key_here

# Optional: Gemini model for live streaming (must support Live API)
# LIVE_MODEL=gemini-2.0-flash-live-001
```

- [ ] **Step 2: Commit**

```bash
git add meta_agent/templates/.env.example
git commit -m "feat(template): add streaming env vars to .env.example"
```

---

### Task 3: Create `streaming.py` template module

**Files:**
- Create: `meta_agent/templates/{{agent_package}}/streaming.py`

- [ ] **Step 1: Write `streaming.py`**

```python
"""
Bidirectional streaming support via Gemini Live API.

Activated when STREAMING_ENABLED=true. Provides a WebSocket endpoint
at /ws/{user_id}/{session_id} for real-time voice/video communication.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.genai import types

logger = logging.getLogger(__name__)


def create_run_config() -> RunConfig:
    """Build RunConfig for bidirectional streaming with Gemini Live API."""
    return RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        session_resumption=types.SessionResumptionConfig(),
    )


async def _upstream_task(
    websocket: WebSocket,
    live_request_queue: LiveRequestQueue,
) -> None:
    """Receive messages from WebSocket client and forward to LiveRequestQueue."""
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "text")

            if msg_type == "text":
                content = types.Content(
                    parts=[types.Part(text=data.get("text", ""))]
                )
                live_request_queue.send_content(content)

            elif msg_type == "audio":
                audio_bytes = base64.b64decode(data["data"])
                audio_blob = types.Blob(
                    mime_type=data.get("mime_type", "audio/pcm;rate=16000"),
                    data=audio_bytes,
                )
                live_request_queue.send_realtime(audio_blob)

            elif msg_type == "activity_end":
                live_request_queue.send_activity_end()

    except WebSocketDisconnect:
        logger.info("Client disconnected (upstream)")
    except Exception as e:
        logger.error("Upstream error: %s", e)


async def _downstream_task(
    websocket: WebSocket,
    runner: Runner,
    user_id: str,
    session_id: str,
    live_request_queue: LiveRequestQueue,
    run_config: RunConfig,
) -> None:
    """Consume events from run_live() and stream to WebSocket client."""
    try:
        async for event in runner.run_live(
            user_id=user_id,
            session_id=session_id,
            live_request_queue=live_request_queue,
            run_config=run_config,
        ):
            await websocket.send_text(
                event.model_dump_json(exclude_none=True, by_alias=True)
            )
    except WebSocketDisconnect:
        logger.info("Client disconnected (downstream)")
    except Exception as e:
        logger.error("Downstream error: %s", e)


def mount_streaming(
    app: FastAPI,
    runner: Runner,
    session_service,
    app_name: str,
) -> None:
    """Register the WebSocket streaming endpoint on the FastAPI app."""

    api_key = os.getenv("API_KEY", "")

    @app.websocket("/ws/{user_id}/{session_id}")
    async def websocket_endpoint(
        websocket: WebSocket,
        user_id: str,
        session_id: str,
        token: str = Query(default=""),
    ) -> None:
        # Auth check
        if api_key and not secrets.compare_digest(token, api_key):
            await websocket.close(code=4001, reason="Unauthorized")
            return

        await websocket.accept()
        logger.info("WebSocket connected: user=%s session=%s", user_id, session_id)

        # Get or create session
        session = await session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if not session:
            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        live_request_queue = LiveRequestQueue()
        run_config = create_run_config()

        try:
            await asyncio.gather(
                _upstream_task(websocket, live_request_queue),
                _downstream_task(
                    websocket, runner, user_id, session_id,
                    live_request_queue, run_config,
                ),
                return_exceptions=True,
            )
        finally:
            live_request_queue.close()
            logger.info("WebSocket closed: user=%s session=%s", user_id, session_id)

    logger.info("Streaming endpoint mounted: /ws/{user_id}/{session_id}")
```

- [ ] **Step 2: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('meta_agent/templates/{{agent_package}}/streaming.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/\{\{agent_package\}\}/streaming.py
git commit -m "feat(template): add streaming.py for Gemini Live API WebSocket"
```

---

### Task 4: Modify `run_adk.py` — add streaming branch

**Files:**
- Modify: `meta_agent/templates/run_adk.py:1-247`

- [ ] **Step 1: Add imports for streaming mode**

After the existing imports (line 6, after `from datetime import...`), add:

```python
from pathlib import Path
```

This is needed for `StaticFiles` directory resolution. The rest of the streaming imports are conditional (inside the `if streaming_enabled` block).

- [ ] **Step 2: Add streaming branch to `main()`**

Replace the `main()` function (lines 163-230) with this version that adds a streaming-enabled path. The non-streaming path remains identical:

```python
def main() -> None:
    # Initialize structured logging
    setup_logging()

    agents_dir = os.getenv("AGENTS_DIR", ".")
    dev_mode = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")
    streaming_enabled = os.getenv("STREAMING_ENABLED", "false").lower() in ("true", "1", "yes")
    port = int(os.getenv("PORT", "8000"))

    print(f"[ADK] Starting server: PORT={port}, DEV_MODE={dev_mode}, STREAMING={streaming_enabled}")

    if streaming_enabled:
        # ── Streaming mode: build app manually for WebSocket support ──
        from fastapi.staticfiles import StaticFiles
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from {{agent_package}}.streaming import mount_streaming
        from {{agent_package}}.agent import root_agent
        from {{agent_package}}.config.llm import LIVE_MODEL

        app = FastAPI(title="{{agent_name}}")

        # Session service
        if dev_mode:
            session_service = InMemorySessionService()
            print("[ADK] STREAMING + DEV mode (in-memory sessions)")
        else:
            from google.adk.sessions import DatabaseSessionService
            session_uri = os.getenv("SESSION_SERVICE_URI")
            if not session_uri:
                raise RuntimeError("SESSION_SERVICE_URI required in production.")
            session_uri = _normalize_to_asyncpg_uri(session_uri)
            session_service = DatabaseSessionService(
                db_url=session_uri,
                connect_args={"ssl": "require"},
            )
            print("[ADK] STREAMING + PRODUCTION mode (database)")

        # Override model to Gemini for live streaming
        live_agent = LlmAgent(
            model=LIVE_MODEL,
            name=root_agent.name,
            description=root_agent.description,
            instruction=root_agent.instruction,
            tools=root_agent.tools,
            sub_agents=root_agent.sub_agents,
        )

        app_name = "{{agent_name}}"
        runner = Runner(
            app_name=app_name,
            agent=live_agent,
            session_service=session_service,
        )

        # Mount streaming WebSocket
        mount_streaming(app, runner, session_service, app_name)

        # Serve test client
        static_dir = Path(__file__).parent / "static"
        if static_dir.is_dir():
            app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            print(f"[ADK] Test client: http://0.0.0.0:{port}/static/test_client.html")

    else:
        # ── Standard mode: use get_fast_api_app (unchanged) ──────────
        if dev_mode:
            print("[ADK] DEVELOPMENT mode (in-memory sessions)")
            app = get_fast_api_app(
                agents_dir=agents_dir,
                session_service_uri=None,
                use_local_storage=False,
                web=False,
                a2a=False,
                host="",
                port=port,
                url_prefix=None,
                reload_agents=True,
                extra_plugins=PLUGIN_PATHS,
            )
        else:
            session_uri = os.getenv("SESSION_SERVICE_URI")
            if not session_uri:
                raise RuntimeError("SESSION_SERVICE_URI is required (set it in .env or env vars).")

            session_uri = _normalize_to_asyncpg_uri(session_uri)
            connect_args = {"ssl": "require"}

            print("[ADK] PRODUCTION mode (database)")
            app = get_fast_api_app(
                agents_dir=agents_dir,
                session_service_uri=session_uri,
                session_db_kwargs={"connect_args": connect_args},
                web=False,
                a2a=False,
                host="",
                port=port,
                url_prefix=None,
                reload_agents=True,
                extra_plugins=PLUGIN_PATHS,
            )

    app.router.redirect_slashes = False

    # Middleware (order matters: outermost first)
    app.add_middleware(RequestIDMiddleware)

    api_key = os.getenv("API_KEY")
    if api_key:
        app.add_middleware(APIKeyMiddleware, api_key=api_key)
        # Disable Swagger/OpenAPI docs in production unless explicitly enabled
        if not os.getenv("DOCS_ENABLED", "").lower() in ("true", "1", "yes"):
            app.openapi_url = None
            app.docs_url = None
            app.redoc_url = None
            print("[ADK] API docs disabled (set DOCS_ENABLED=true to enable)")
        print("[ADK] API key authentication enabled")
    else:
        print("[ADK] WARNING: No API_KEY set — endpoints are unauthenticated")

    add_endpoints(app)

    print(f"[ADK] Server ready: http://0.0.0.0:{port}")
    uvicorn.run(app, host="", port=port)
```

- [ ] **Step 3: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('meta_agent/templates/run_adk.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add meta_agent/templates/run_adk.py
git commit -m "feat(template): add streaming branch to run_adk.py with manual Runner"
```

---

### Task 5: Create test client HTML

**Files:**
- Create: `meta_agent/templates/static/test_client.html`

- [ ] **Step 1: Write `test_client.html`**

Single HTML file with inline CSS/JS. Features:
- WebSocket URL input with user_id/session_id fields
- Connect/Disconnect button
- Mic toggle button (requests `getUserMedia` on first click)
- Audio capture: `AudioContext` + `ScriptProcessorNode` → PCM 16kHz 16-bit mono → base64 → WebSocket
- Audio playback: receives audio events → `AudioContext` → speakers
- Text input field for sending text messages
- Chat log div showing transcriptions (input/output)
- Connection status indicator (dot: gray/green/red)
- Token field for auth
- No external dependencies

The HTML should be self-contained (~250 lines). Key sections:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Voice Agent Test Client</title>
    <style>
        /* Minimal styles: dark theme, monospace, status indicator */
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 20px; max-width: 800px; margin: 0 auto; }
        h1 { font-size: 1.2em; margin-bottom: 16px; color: #a0a0ff; }
        .controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
        .controls input { background: #16213e; border: 1px solid #333; color: #e0e0e0; padding: 6px 10px; border-radius: 4px; font-family: monospace; }
        .controls input:focus { border-color: #a0a0ff; outline: none; }
        button { background: #0f3460; border: 1px solid #444; color: #e0e0e0; padding: 8px 16px; border-radius: 4px; cursor: pointer; font-family: monospace; }
        button:hover { background: #1a4a80; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        button.active { background: #e94560; border-color: #e94560; }
        .status { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
        .status.disconnected { background: #666; }
        .status.connected { background: #4caf50; }
        .status.error { background: #e94560; }
        #chat { background: #16213e; border: 1px solid #333; border-radius: 4px; padding: 12px; height: 400px; overflow-y: auto; margin-top: 12px; font-size: 0.9em; line-height: 1.6; }
        .msg { margin-bottom: 4px; }
        .msg.user { color: #82b1ff; }
        .msg.agent { color: #a5d6a7; }
        .msg.system { color: #888; font-style: italic; }
        .text-input { display: flex; gap: 8px; margin-top: 8px; }
        .text-input input { flex: 1; }
    </style>
</head>
<body>
    <h1>Voice Agent Test Client</h1>

    <div class="controls">
        <span class="status disconnected" id="statusDot"></span>
        <input id="host" value="ws://localhost:8000" placeholder="WebSocket host" style="width:200px">
        <input id="userId" value="test-user" placeholder="User ID" style="width:120px">
        <input id="sessionId" value="session-1" placeholder="Session ID" style="width:120px">
        <input id="token" type="password" placeholder="API Key (optional)" style="width:160px">
    </div>
    <div class="controls">
        <button id="connectBtn" onclick="toggleConnection()">Connect</button>
        <button id="micBtn" onclick="toggleMic()" disabled>🎤 Mic Off</button>
    </div>

    <div class="text-input">
        <input id="textInput" placeholder="Type a message..." onkeydown="if(event.key==='Enter')sendText()" disabled>
        <button id="sendBtn" onclick="sendText()" disabled>Send</button>
    </div>

    <div id="chat"></div>

    <script>
        let ws = null;
        let audioContext = null;
        let micStream = null;
        let processor = null;
        let micActive = false;
        let playbackCtx = null;

        const chat = document.getElementById('chat');
        const statusDot = document.getElementById('statusDot');
        const connectBtn = document.getElementById('connectBtn');
        const micBtn = document.getElementById('micBtn');
        const textInput = document.getElementById('textInput');
        const sendBtn = document.getElementById('sendBtn');

        function log(text, cls = 'system') {
            const div = document.createElement('div');
            div.className = `msg ${cls}`;
            div.textContent = text;
            chat.appendChild(div);
            chat.scrollTop = chat.scrollHeight;
        }

        function setStatus(state) {
            statusDot.className = `status ${state}`;
        }

        function toggleConnection() {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.close();
                return;
            }
            const host = document.getElementById('host').value;
            const userId = document.getElementById('userId').value;
            const sessionId = document.getElementById('sessionId').value;
            const token = document.getElementById('token').value;

            const url = `${host}/ws/${userId}/${sessionId}${token ? '?token=' + encodeURIComponent(token) : ''}`;
            log(`Connecting to ${url}...`);

            ws = new WebSocket(url);
            ws.onopen = () => {
                setStatus('connected');
                connectBtn.textContent = 'Disconnect';
                micBtn.disabled = false;
                textInput.disabled = false;
                sendBtn.disabled = false;
                log('Connected.');
            };
            ws.onclose = (e) => {
                setStatus('disconnected');
                connectBtn.textContent = 'Connect';
                micBtn.disabled = true;
                textInput.disabled = true;
                sendBtn.disabled = true;
                stopMic();
                log(`Disconnected (code=${e.code}).`);
            };
            ws.onerror = () => {
                setStatus('error');
                log('WebSocket error.');
            };
            ws.onmessage = (e) => {
                try {
                    const event = JSON.parse(e.data);
                    handleEvent(event);
                } catch (err) {
                    log(`Raw: ${e.data}`);
                }
            };
        }

        function handleEvent(event) {
            // Display text transcriptions
            if (event.content && event.content.parts) {
                for (const part of event.content.parts) {
                    if (part.text) {
                        const role = event.content.role === 'user' ? 'user' : 'agent';
                        log(`${role}: ${part.text}`, role);
                    }
                    if (part.inline_data && part.inline_data.mime_type?.startsWith('audio/')) {
                        playAudio(part.inline_data.data, part.inline_data.mime_type);
                    }
                }
            }
            // Handle server audio in other event shapes
            if (event.server_content?.model_turn?.parts) {
                for (const part of event.server_content.model_turn.parts) {
                    if (part.inline_data && part.inline_data.mime_type?.startsWith('audio/')) {
                        playAudio(part.inline_data.data, part.inline_data.mime_type);
                    }
                    if (part.text) {
                        log(`agent: ${part.text}`, 'agent');
                    }
                }
            }
        }

        function playAudio(base64Data, mimeType) {
            if (!playbackCtx) playbackCtx = new AudioContext({ sampleRate: 24000 });
            const bytes = Uint8Array.from(atob(base64Data), c => c.charCodeAt(0));
            // Assume PCM 16-bit signed LE mono at 24kHz
            const samples = new Float32Array(bytes.length / 2);
            const view = new DataView(bytes.buffer);
            for (let i = 0; i < samples.length; i++) {
                samples[i] = view.getInt16(i * 2, true) / 32768;
            }
            const buffer = playbackCtx.createBuffer(1, samples.length, 24000);
            buffer.getChannelData(0).set(samples);
            const source = playbackCtx.createBufferSource();
            source.buffer = buffer;
            source.connect(playbackCtx.destination);
            source.start();
        }

        function sendText() {
            const text = textInput.value.trim();
            if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
            ws.send(JSON.stringify({ type: 'text', text }));
            log(`you: ${text}`, 'user');
            textInput.value = '';
        }

        async function toggleMic() {
            if (micActive) {
                stopMic();
            } else {
                await startMic();
            }
        }

        async function startMic() {
            try {
                micStream = await navigator.mediaDevices.getUserMedia({
                    audio: { sampleRate: 16000, channelCount: 1, echoCancellation: true }
                });
                audioContext = new AudioContext({ sampleRate: 16000 });
                const source = audioContext.createMediaStreamSource(micStream);

                // ScriptProcessorNode for broad compatibility
                processor = audioContext.createScriptProcessor(4096, 1, 1);
                processor.onaudioprocess = (e) => {
                    if (!ws || ws.readyState !== WebSocket.OPEN) return;
                    const float32 = e.inputBuffer.getChannelData(0);
                    const pcm16 = new Int16Array(float32.length);
                    for (let i = 0; i < float32.length; i++) {
                        pcm16[i] = Math.max(-32768, Math.min(32767, Math.round(float32[i] * 32768)));
                    }
                    const base64 = btoa(String.fromCharCode(...new Uint8Array(pcm16.buffer)));
                    ws.send(JSON.stringify({
                        type: 'audio',
                        mime_type: 'audio/pcm;rate=16000',
                        data: base64,
                    }));
                };
                source.connect(processor);
                processor.connect(audioContext.destination);

                micActive = true;
                micBtn.textContent = '🎤 Mic On';
                micBtn.classList.add('active');
                log('Microphone started.');
            } catch (err) {
                log(`Mic error: ${err.message}`);
            }
        }

        function stopMic() {
            if (processor) { processor.disconnect(); processor = null; }
            if (audioContext) { audioContext.close(); audioContext = null; }
            if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
            micActive = false;
            micBtn.textContent = '🎤 Mic Off';
            micBtn.classList.remove('active');
        }
    </script>
</body>
</html>
```

- [ ] **Step 2: Verify the HTML is well-formed**

Run: `python -c "from html.parser import HTMLParser; HTMLParser().feed(open('meta_agent/templates/static/test_client.html').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add meta_agent/templates/static/test_client.html
git commit -m "feat(template): add minimal voice test client HTML"
```

---

### Task 6: Create `adk-streaming` skill

**Files:**
- Create: `meta_agent/skills/adk-streaming/SKILL.md`
- Create: `meta_agent/skills/adk-streaming/references/streaming-patterns.md`
- Create: `meta_agent/skills/adk-streaming/references/live-api-reference.md`

- [ ] **Step 1: Create SKILL.md**

```markdown
---
name: adk-streaming
description: >-
  Building voice and video agents with Gemini Live API — bidirectional
  streaming via WebSocket, LiveRequestQueue, run_live(), RunConfig, and
  audio/video patterns. Load when the user wants a voice agent, live
  agent, or real-time audio/video capabilities.
---

# ADK Streaming — Voice & Video Agents

Build real-time voice and video agents using ADK's bidirectional streaming
with the Gemini Live API.

## When to Use

The user explicitly asks for:
- Voice agent / voice assistant
- Live agent / real-time agent
- Audio/video capabilities
- Conversational agent with speech

## Architecture

```
Browser (mic/cam) → WebSocket /ws/{user_id}/{session_id}
    ↓ upstream_task
LiveRequestQueue (text: send_content, audio: send_realtime)
    ↓
Runner.run_live() ←→ Gemini Live API (BIDI mode)
    ↓ downstream_task
WebSocket → Browser (audio playback + transcriptions)
```

## Key Components

| Component | Purpose |
|-----------|---------|
| `LiveRequestQueue` | Async FIFO buffer between WebSocket and agent. One per session. |
| `Runner.run_live()` | Async generator consuming from queue, yielding events |
| `RunConfig(streaming_mode=StreamingMode.BIDI)` | Configures BIDI mode for Live API |
| `StreamingMode.BIDI` | WebSocket to Gemini Live API (audio/video) |
| `StreamingMode.SSE` | HTTP streaming to standard Gemini API (text only) |

## Template Integration

The generated template includes streaming support gated behind `STREAMING_ENABLED=true`:

1. `streaming.py` — WebSocket handler with upstream/downstream tasks
2. `run_adk.py` — conditional branch that builds Runner manually with `LIVE_MODEL`
3. `static/test_client.html` — minimal browser test client
4. `config/llm.py` — `LIVE_MODEL` config (Gemini model string, not LiteLLM)

## Design Decisions for Streaming Agents

When designing a streaming agent:

1. **Model**: Must use Gemini directly (`LIVE_MODEL`), not OpenRouter/LiteLLM
2. **API Key**: Requires `GOOGLE_API_KEY` (not `OPENROUTER_API_KEY`)
3. **Tools work during streams**: Agent can call FunctionTools mid-conversation
4. **VAD**: Automatic by default — no manual activity signals needed
5. **Transcription**: Enable both input and output transcription for logging
6. **Session resumption**: Enable for connection recovery

## Customization Points

When generating a streaming agent, customize these in `streaming.py`:

- **Voice**: Add `speech_config` to `RunConfig` for voice selection
- **Language**: Set `language_code` in speech config
- **VAD mode**: Disable auto VAD for push-to-talk UIs
- **Video**: Add video modality to `response_modalities`
- **Audio format**: Default is PCM 16kHz input, 24kHz output

Load references for details on each customization.

## References

- Load `streaming-patterns` for RunConfig options, VAD modes, voice selection, and audio format details.
- Load `live-api-reference` for LiveRequestQueue API, event types, and error handling.
```

- [ ] **Step 2: Create `references/streaming-patterns.md`**

```markdown
# Streaming Patterns

## RunConfig Options

### Basic Audio (default)

```python
from google.adk.agents.run_config import RunConfig, StreamingMode

run_config = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    session_resumption=types.SessionResumptionConfig(),
)
```

### Custom Voice

```python
from google.genai import types

run_config = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name="Aoede"  # Options: Aoede, Charon, Fenrir, Kore, Puck
            )
        ),
        language_code="en-US",
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)
```

### Text-Only Streaming (SSE)

For text streaming without voice, use SSE mode (works with any model):

```python
run_config = RunConfig(
    streaming_mode=StreamingMode.SSE,
    response_modalities=["TEXT"],
)
```

## VAD Modes

### Automatic VAD (default — recommended)

VAD is enabled by default. The Live API detects speech boundaries automatically.
No `send_activity_start()` or `send_activity_end()` needed.

```python
# Just stream audio continuously
while audio_available:
    audio_chunk = get_audio_chunk()
    live_request_queue.send_realtime(audio_chunk)
```

### Manual VAD (push-to-talk)

Disable automatic VAD for push-to-talk UIs:

```python
run_config = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=True
        )
    ),
)

# Client must send activity signals
live_request_queue.send_activity_start()
# ... stream audio ...
live_request_queue.send_activity_end()
```

## Audio Formats

| Direction | Format | Sample Rate | Bit Depth | Channels |
|-----------|--------|-------------|-----------|----------|
| Input (mic → server) | PCM signed LE | 16,000 Hz | 16-bit | Mono |
| Output (server → speaker) | PCM signed LE | 24,000 Hz | 16-bit | Mono |

MIME types:
- Input: `audio/pcm;rate=16000`
- Output: `audio/pcm;rate=24000`

## Tool Calling During Streams

Tools work during live sessions. The agent can call FunctionTools mid-conversation
and the results are streamed back. No special configuration needed — tools defined
in the agent work automatically.

## Session Resumption

Enable session resumption to handle connection drops gracefully:

```python
run_config = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    session_resumption=types.SessionResumptionConfig(),
)
```

The client receives a session resumption token in events. On reconnect, send it
back to resume from where the conversation left off.
```

- [ ] **Step 3: Create `references/live-api-reference.md`**

```markdown
# Live API Reference

## LiveRequestQueue

Thread-safe async queue for sending messages to the agent during streaming.

```python
from google.adk.agents.live_request_queue import LiveRequestQueue

queue = LiveRequestQueue()
```

### Methods

| Method | Purpose | When to Use |
|--------|---------|-------------|
| `send_content(content)` | Send text message | User types text |
| `send_realtime(blob)` | Send audio/video blob | Streaming mic/cam data |
| `send_activity_start()` | Signal speech started | Only with manual VAD |
| `send_activity_end()` | Signal speech ended | Only with manual VAD |
| `close()` | Close the queue | Session termination (always call in finally) |

### send_content

```python
from google.genai import types

content = types.Content(parts=[types.Part(text="Hello")])
queue.send_content(content)
```

### send_realtime

```python
audio_blob = types.Blob(
    mime_type="audio/pcm;rate=16000",
    data=audio_bytes,  # raw PCM bytes
)
queue.send_realtime(audio_blob)
```

## Runner.run_live

Async generator that processes the queue and yields events.

```python
async for event in runner.run_live(
    user_id=user_id,
    session_id=session_id,
    live_request_queue=queue,
    run_config=run_config,
):
    # event is an ADK Event object
    json_str = event.model_dump_json(exclude_none=True, by_alias=True)
```

## Event Types

Events from `run_live()` contain various fields:

| Field | Content |
|-------|---------|
| `event.content` | Agent's response (text parts, audio inline_data) |
| `event.server_content.model_turn.parts` | Model output during streaming |
| `event.tool_calls` | Tool invocations during the session |
| `event.actions` | State changes, session updates |

### Checking for Audio

```python
if event.content and event.content.parts:
    for part in event.content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("audio/"):
            # Audio data in part.inline_data.data (base64)
            pass
        if part.text:
            # Text transcription
            pass
```

## Error Handling

| Error | Cause | Recovery |
|-------|-------|----------|
| `WebSocketDisconnect` | Client closed connection | Close queue, clean up |
| `ConnectionError` | Network issue | Retry with session resumption |
| `InvalidArgument` | Bad RunConfig | Check model supports Live API |
| Queue closed | Session ended | Create new queue for new session |

## Important Notes

1. **One queue per session** — create a new `LiveRequestQueue` for each WebSocket connection
2. **Always close the queue** — use `finally` block to call `queue.close()`
3. **Gemini models only** — BIDI mode requires Gemini models (e.g., `gemini-2.0-flash-live-001`)
4. **Concurrent tasks** — always run upstream and downstream with `asyncio.gather()`
```

- [ ] **Step 4: Verify SKILL.md parses**

Run: `python -c "
import yaml
text = open('meta_agent/skills/adk-streaming/SKILL.md').read()
_, fm, _ = text.split('---', 2)
data = yaml.safe_load(fm)
assert data['name'] == 'adk-streaming'
print('OK:', data['name'])
"`
Expected: `OK: adk-streaming`

- [ ] **Step 5: Commit**

```bash
git add meta_agent/skills/adk-streaming/
git commit -m "feat: add adk-streaming skill for voice/video agent guidance"
```

---

### Task 7: Update meta-agent `instructions.py`

**Files:**
- Modify: `meta_agent/prompt/instructions.py:31-76`

- [ ] **Step 1: Add voice/video to Discovery (Step 1)**

After line 37 (`- **LLM preference**: Model preference...`), add:

```
- **Voice/Video**: Does this agent need voice or live video capabilities?
  (Ask when user mentions voice agents, live agents, or real-time audio/video)
```

- [ ] **Step 2: Add streaming to Design (Step 2)**

After line 48 (`load_skill_resource("adk-skill-design-patterns", "pattern-<name>.md")`), add:

```
- If the user wants voice/video capabilities, load `load_skill("adk-streaming")` for streaming architecture guidance.
```

- [ ] **Step 3: Add streaming skill to Generate (Step 4)**

After line 75 (the `adk-skill-design-patterns` loading line), add:

```
- `load_skill("adk-streaming")` before configuring voice/video agents
  → then `load_skill_resource("adk-streaming", "streaming-patterns.md")` for RunConfig customization
```

- [ ] **Step 4: Verify the file parses**

Run: `python -c "import ast; ast.parse(open('meta_agent/prompt/instructions.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add meta_agent/prompt/instructions.py
git commit -m "feat: add voice/video streaming guidance to meta-agent workflow"
```

---

### Task 8: Add `.html` to scaffold TEXT_EXTENSIONS

**Files:**
- Modify: `scaffold.py:26-29`

- [ ] **Step 1: Add `.html` to TEXT_EXTENSIONS**

In `scaffold.py`, add `.html` to the `TEXT_EXTENSIONS` frozenset:

```python
TEXT_EXTENSIONS = frozenset({
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml",
    ".cfg", ".ini", ".env", ".example", ".html",
})
```

- [ ] **Step 2: Commit**

```bash
git add scaffold.py
git commit -m "feat: add .html to scaffold TEXT_EXTENSIONS for template substitution"
```

---

### Task 9: End-to-end scaffold test

- [ ] **Step 1: Scaffold a test agent**

Run:
```bash
python scaffold.py test-streaming-agent --output-dir /tmp/test-streaming --description "Test streaming agent"
```
Expected: `Agent scaffolded at: /tmp/test-streaming/test-streaming-agent`

- [ ] **Step 2: Verify streaming files exist**

Run:
```bash
ls -la /tmp/test-streaming/test-streaming-agent/test_streaming_agent/streaming.py
ls -la /tmp/test-streaming/test-streaming-agent/static/test_client.html
```
Expected: Both files exist

- [ ] **Step 3: Verify placeholders are replaced**

Run:
```bash
grep -c '{{agent_package}}' /tmp/test-streaming/test-streaming-agent/test_streaming_agent/streaming.py
grep -c '{{agent_package}}' /tmp/test-streaming/test-streaming-agent/run_adk.py
```
Expected: `0` for both (no unresolved placeholders)

- [ ] **Step 4: Verify Python files parse**

Run:
```bash
cd /tmp/test-streaming/test-streaming-agent
python -c "import ast; ast.parse(open('run_adk.py').read()); print('run_adk.py OK')"
python -c "import ast; ast.parse(open('test_streaming_agent/streaming.py').read()); print('streaming.py OK')"
python -c "import ast; ast.parse(open('test_streaming_agent/config/llm.py').read()); print('llm.py OK')"
```
Expected: All OK

- [ ] **Step 5: Verify .env.example has streaming section**

Run:
```bash
grep 'STREAMING_ENABLED' /tmp/test-streaming/test-streaming-agent/.env.example
grep 'LIVE_MODEL' /tmp/test-streaming/test-streaming-agent/.env.example
grep 'GOOGLE_API_KEY' /tmp/test-streaming/test-streaming-agent/.env.example
```
Expected: All three found

- [ ] **Step 6: Clean up**

Run:
```bash
rm -rf /tmp/test-streaming
```

No commit needed — this was a verification step.
