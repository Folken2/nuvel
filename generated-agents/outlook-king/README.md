# outlook-king

> The agent that lives inside Microsoft Outlook. Searches like nobody. Drafts in your voice. Coaches your drafts as you write. Learns you over time.

Built on Google ADK with [nuvel](https://github.com/Folken2/nuvel), wired to Outlook through Composio's Tool Router (MCP) plus an Office.js add-in for in-app context.

## What it does

| Capability | How it works |
|---|---|
| **Search like nobody** | Composio's `OUTLOOK_*` toolkit (Microsoft Graph behind the scenes). Natural language → structured filters via `plan_email_search`, then re-ranked by recency + sender weight via `rank_search_hits`. |
| **Draft generation in your voice** | Reads the writing-style topic from markdown memory, mirrors your opener, sign-off, sentence shape, and contraction habits. Returns plain text ready to insert into Outlook compose. |
| **Live coaching on your drafts** | The taskpane pushes the current compose body into session state on every edit. `analyze_draft` gives objective metrics (hedges, passives, long sentences); the agent combines them with your style memory to deliver short, grounded, voice-aware feedback. |
| **Style-memory learning loop** | Every sent email calls `learn_style_from_sent_email` → appends a structured fingerprint to the `writing-style` topic. After enough samples, the agent calls `consolidate_writing_style` to compress fingerprints into a tight rulebook the drafting and coaching tools apply. |

The agent's character is also self-evolving (`--persona` was on at scaffold time): SOUL.md and accumulated skills are read fresh each turn, so outlook-king genuinely sharpens over weeks of use.

## Architecture

```
┌──────────────────────────────┐
│  Microsoft Outlook           │
│  (Desktop / Web / Mac)       │
│  ┌────────────────────────┐  │
│  │  Add-in (Office.js)    │  │   ←── manifest.xml in /addin
│  │  • Taskpane (React)    │  │
│  │  • Coach ribbon button │  │
│  │  • Reads compose +     │  │
│  │    selected message    │  │
│  └──────┬─────────────────┘  │
└─────────┼────────────────────┘
          │ HTTPS  (POST /api/outlook/*)
          ▼
┌──────────────────────────────┐
│  Backend bridge (FastAPI)    │   ←── /backend/main.py
│  • /context  — push Outlook  │
│    state into ADK session    │
│  • /chat     — run agent     │
│  • /chat/stream  — SSE       │
│  • /learn-sent — append      │
│    style fingerprint         │
└─────────┬────────────────────┘
          │
          ▼
┌──────────────────────────────┐
│  outlook-king (Google ADK)   │   ←── /outlook_king
│  • Domain tools              │
│  • Composio MCP toolset      │   ←── ~1000 toolkits incl. OUTLOOK_*
│  • Skills (SKILL.md)         │
│  • Markdown memory           │
│  • Self-evolving SOUL.md     │   ←── persona overlay
└──────────────────────────────┘
```

## Running it

### 1. Agent + backend

```bash
cd generated-agents/outlook-king

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# OPENROUTER_API_KEY=...   (or GEMINI_API_KEY)
# COMPOSIO_API_KEY=...     (enables OUTLOOK_* toolkit via Composio MCP)
# CORS_ORIGINS=https://localhost:3000,http://localhost:3000

uvicorn backend.main:app --reload --port 8000
```

> **Composio setup:** in the Composio dashboard, connect your Microsoft / Outlook account once. Composio handles OAuth; the agent picks up the toolkit via `composio.create(user_id=...)`. Without `COMPOSIO_API_KEY` the agent still runs, but search is limited to the currently-selected message.

### 2. Outlook add-in

```bash
cd addin
npm install
npm run dev-server
```

Sideload `manifest.xml`:

- **Outlook on the web** → Settings → Mail → Customize actions → Get Add-ins → My add-ins → Add a custom add-in → From file.
- **Outlook desktop (Win/Mac)** → Get Add-ins → My add-ins → Add a custom add-in → From file.
- **Microsoft 365 admin tenant** → Integrated apps → Upload custom apps.

In Outlook you'll see an **outlook-king** group on the Home tab, in both read and compose modes:
- **Open outlook-king** — taskpane chat with quick actions and a context strip showing what the agent currently sees.
- **Coach my draft** — one-click voice-aware feedback while composing.

### 3. Test the heuristic tools (no LLM cost)

```bash
pytest tests/test_outlook_tools.py tests/test_style_tools.py -v
```

LLM-touching tests in `tests/test_agent.py` use ADK record/replay — see that file's docstring.

## Outlook-specific tools

| Tool | Purpose |
|---|---|
| `get_current_compose` | Read the user's open compose window from session state |
| `get_selected_message` | Read the user's currently-selected inbox message |
| `analyze_draft` | Objective metrics on a draft (counts, hedges, passives, structure) |
| `plan_email_search` | Natural language → structured Outlook filters |
| `rank_search_hits` | Re-rank Composio results by recency + sender weight |
| `recall_writing_style` | Read consolidated voice rulebook from markdown memory |
| `learn_style_from_sent_email` | Append a structured fingerprint after a send |
| `consolidate_writing_style` | Distill fingerprints into a rulebook |

Plus everything the nuvel scaffold ships: `save_memory`/`recall_memory`, `cronjob`, `read_soul`/`update_soul`, `author_skill`, and the Composio MCP toolset.

## Skills the agent reads at runtime

- `outlook-search-via-composio` — three-step pattern (plan → execute → rank), widen/narrow tactics, common pitfalls.
- `email-coaching` — coaching rubric in priority order. Quote, don't paraphrase. Two-to-three points max.
- `voice-matching` — load style first, mirror inbound register, never invent recipients or dates.
- `style-learning-loop` — collect fingerprints → consolidate rulebook → fold in edits.

`LazySkillToolset` reloads them on mtime change — edit a SKILL.md and it takes effect on the next agent invocation, no process restart.

## File map

```
generated-agents/outlook-king/
├── outlook_king/                     # the ADK agent package
│   ├── agent.py
│   ├── prompt/instructions.py        # outlook-specific frame
│   ├── tools/
│   │   ├── outlook_context.py        # NEW
│   │   ├── style_tools.py            # NEW
│   │   ├── coach_tools.py            # NEW
│   │   ├── search_hints.py           # NEW
│   │   ├── composio_mcp.py
│   │   ├── memory_tools.py
│   │   ├── soul_tools.py / skill_tools.py / awakening_tools.py
│   │   └── __init__.py               # wires all of the above
│   ├── skills/
│   │   ├── outlook-search-via-composio/SKILL.md
│   │   ├── email-coaching/SKILL.md
│   │   ├── voice-matching/SKILL.md
│   │   └── style-learning-loop/SKILL.md
│   └── soul/
│       ├── SOUL.md                   # outlook-king character
│       └── AWAKENING.md
├── backend/                          # NEW — FastAPI bridge
│   ├── main.py
│   └── README.md
├── addin/                            # NEW — Outlook add-in (Office.js + React)
│   ├── manifest.xml
│   ├── package.json
│   ├── webpack.config.js
│   └── src/
│       ├── taskpane/                 # chat + context strip + insert/replace
│       ├── commands/                 # "Coach my draft" ribbon action
│       └── config/api.ts
├── tests/
│   ├── test_outlook_tools.py         # NEW
│   ├── test_style_tools.py           # NEW
│   └── test_agent.py
├── memory/                           # markdown memory
├── requirements.txt
└── README.md (this file)
```

## Where to look first

- Change how it coaches → `skills/email-coaching/SKILL.md` + `tools/coach_tools.py`.
- Change how it drafts → `skills/voice-matching/SKILL.md` + `memory/topics/writing-style.md` (regenerated by the learning loop).
- Change search behavior → `skills/outlook-search-via-composio/SKILL.md` + `tools/search_hints.py`.
- Change the UI → `addin/src/taskpane/components/App.tsx`.
- Change the API → `backend/main.py`.

## What's not in v1

- **OnMessageSend auto-learning**: the manifest doesn't yet register a `LaunchEvent: OnMessageSend` handler, so style learning fires manually from the taskpane today. Wire it in `commands.ts` and add a `LaunchEvent` extension point in `manifest.xml`.
- **Production hosting**: the manifest URLs point at `https://localhost:3000`. Swap to your Vercel / Azure Static Web Apps origin for production builds.
- **Per-recipient style memories**: the scaffolding is there — call `save_memory(topic="recipient-<addr>", …)` — but consolidation doesn't yet auto-build them from the fingerprint stream.
