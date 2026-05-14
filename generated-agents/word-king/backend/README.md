# word-king — backend bridge

Thin FastAPI server that connects the Word add-in (Office.js) to the ADK agent.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness probe |
| POST | `/api/word/context` | Push current selection + full document into session state |
| POST | `/api/word/chat` | Run one agent turn, return final text |
| POST | `/api/word/chat/stream` | Same, SSE-streamed (tool_start, tool_end, final, done) |
| POST | `/api/word/learn-passage` | Record style fingerprint after the user accepts a draft |

## Run

```bash
cd generated-agents/word-king
cp .env.example .env  # add OPENROUTER_API_KEY (+ COMPOSIO_API_KEY if you wire it)
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

If `BACKEND_API_KEY` is set in `.env`, the taskpane must send it as
`X-API-Key` on every `/api/*` call.

## Request shapes

All POST bodies are JSON. `session_id` is a stable per-installation UUID
the add-in generates on first run and reuses. Example chat call:

```json
{
  "session_id": "8c1a…",
  "user_id": "alice@example.com",
  "prompt": "Rewrite my selection in a tighter, more formal voice.",
  "selection": {
    "text": "We were sort of thinking maybe we could perhaps push the deadline.",
    "paragraph_count": 1,
    "word_count": 13,
    "style_name": "Normal"
  },
  "document": {
    "text": "…the entire document body…",
    "paragraph_count": 14,
    "word_count": 1820,
    "style_name": null
  }
}
```

Either `selection` or `document` (or both) may be omitted on a given
call; the backend only writes what it receives, so existing session
state survives a partial update.
