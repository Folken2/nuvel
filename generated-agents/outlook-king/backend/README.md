# outlook-king — backend bridge

Thin FastAPI server that connects the Outlook add-in (Office.js) to the ADK agent.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness probe |
| POST | `/api/outlook/context` | Push current compose + selected message into session state |
| POST | `/api/outlook/chat` | Run one agent turn, return final text |
| POST | `/api/outlook/chat/stream` | Same, SSE-streamed (tool_start, tool_end, final, done) |
| POST | `/api/outlook/learn-sent` | Record style fingerprint after a send |

## Run

```bash
cd generated-agents/outlook-king
cp .env.example .env  # add OPENROUTER_API_KEY (+ COMPOSIO_API_KEY for Outlook search)
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
  "prompt": "Draft a reply that's friendlier",
  "compose": {
    "body": "Hi, attaching the report.",
    "subject": "RE: Q3 report",
    "to": ["bob@example.com"],
    "cc": [],
    "mode": "reply",
    "conversation_id": "AAQk…"
  }
}
```
