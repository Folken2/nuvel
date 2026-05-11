# ppt-king — backend bridge

Thin FastAPI server that connects the PowerPoint add-in (Office.js) to the ADK agent.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness probe |
| POST | `/api/ppt/context` | Push current slide + deck outline into session state |
| POST | `/api/ppt/chat` | Run one agent turn, return final text |
| POST | `/api/ppt/chat/stream` | Same, SSE-streamed (tool_start, tool_end, final, done) |
| POST | `/api/ppt/learn-slide` | Record deck-style fingerprint after a kept slide |

## Run

```bash
cd generated-agents/ppt-king
cp .env.example .env  # add OPENROUTER_API_KEY (+ COMPOSIO_API_KEY if you want extra toolkits)
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
  "prompt": "Tighten this slide. Apply the rubric.",
  "current_slide": {
    "index": 3,
    "title": "Q3 Revenue",
    "bullets": ["Revenue grew", "Costs went up", "Margin held"],
    "notes": "",
    "layout_name": "Title and Content"
  },
  "deck_outline": {
    "slide_count": 10,
    "slides": [
      {"index": 0, "title": "Title", "bullet_count": 0, "has_notes": false},
      {"index": 1, "title": "Agenda", "bullet_count": 4, "has_notes": false}
    ]
  }
}
```

The `current_slide` and `deck_outline` fields are both optional — send
whichever the user's PowerPoint state currently exposes.
