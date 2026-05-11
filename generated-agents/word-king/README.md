# word-king

> The agent that lives inside Microsoft Word. Drafts in your voice. Rewrites without overstepping. Learns you over time.

Built on Google ADK with [nuvel](https://github.com/Folken2/nuvel), wired to Word through an Office.js add-in for in-app context and a thin FastAPI bridge in the middle.

## What it does

| Capability | How it works |
|---|---|
| **Draft new sections in your voice** | Reads the writing-style topic from markdown memory, mirrors your paragraph length, register, terminology, and punctuation tics. For anything over ~300 words, calls `propose_section_outline` first to lock structure before prose. Returns plain text the add-in inserts at the cursor. |
| **Rewrite selected text per a precise instruction** | `rewrite_passage_hints` classifies the ask (`minimal-fix`, `shorten`, `expand`, `clarify`, `raise-register`, etc.) and returns objective metrics plus a length window. The agent honors the classified ask — a typo fix is a typo fix, not a paragraph rewrite — and preserves quoted spans, citations, and technical terminology verbatim. |
| **Style-memory learning loop** | Every accepted draft (and every selection-replace via the ribbon) calls `learn_style_from_passage` → appends a structured fingerprint to the `writing-style` topic. After enough samples, the agent calls `consolidate_writing_style` to compress fingerprints into a tight rulebook the drafting and rewrite steps apply. |

The agent's character is also self-evolving (`--persona` was on at scaffold time): SOUL.md and accumulated skills are read fresh each turn, so word-king genuinely sharpens over weeks of use.

## Architecture

```
┌──────────────────────────────┐
│  Microsoft Word              │
│  (Desktop / Web / Mac)       │
│  ┌────────────────────────┐  │
│  │  Add-in (Office.js)    │  │   ←── manifest.xml in /addin
│  │  • Taskpane (React)    │  │
│  │  • Rewrite ribbon btn  │  │
│  │  • Reads current       │  │
│  │    selection + body    │  │
│  └──────┬─────────────────┘  │
└─────────┼────────────────────┘
          │ HTTPS  (POST /api/word/*)
          ▼
┌──────────────────────────────┐
│  Backend bridge (FastAPI)    │   ←── /backend/main.py
│  • /context  — push Word     │
│    state into ADK session    │
│  • /chat     — run agent     │
│  • /chat/stream  — SSE       │
│  • /learn-passage — append   │
│    style fingerprint         │
└─────────┬────────────────────┘
          │
          ▼
┌──────────────────────────────┐
│  word-king (Google ADK)      │   ←── /word_king
│  • Domain tools              │
│  • Composio MCP toolset      │   ←── generic outbound
│  • Skills (SKILL.md)         │
│  • Markdown memory           │
│  • Self-evolving SOUL.md     │   ←── persona overlay
└──────────────────────────────┘
```

## Running it

### 1. Agent + backend

```bash
cd generated-agents/word-king

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# OPENROUTER_API_KEY=...   (or GEMINI_API_KEY)
# COMPOSIO_API_KEY=...     (optional — enables generic Composio MCP)
# CORS_ORIGINS=https://localhost:3000,http://localhost:3000

uvicorn backend.main:app --reload --port 8000
```

### 2. Word add-in

```bash
cd addin
npm install
npm run dev-server
```

Sideload `manifest.xml`:

- **Word on the web** → Insert → My Add-ins → Upload My Add-in → choose `manifest.xml`.
- **Word desktop (Win/Mac)** → Insert → My Add-ins → Manage My Add-ins → Upload My Add-in → from file.
- **Microsoft 365 admin tenant** → Integrated apps → Upload custom apps.

In Word you'll see a **word-king** group on the Home tab:
- **Open word-king** — taskpane chat with quick actions and a context strip showing what the agent currently sees (current selection size, document size).
- **Rewrite selection** — one-click voice-aware rewrite of whatever's currently highlighted.

### 3. Test the heuristic tools (no LLM cost)

```bash
pytest tests/test_word_tools.py tests/test_style_tools.py -v
```

LLM-touching tests in `tests/test_agent.py` use ADK record/replay — see that file's docstring.

## Word-specific tools

| Tool | Purpose |
|---|---|
| `get_current_selection` | Read the user's currently-selected text from session state |
| `get_full_document` | Read the user's full document body from session state |
| `propose_section_outline` | Heuristic outline (headings + scope + target words) for a new section |
| `rewrite_passage_hints` | Objective metrics on a passage (counts, longest sentence, hedges, passives, quotes, citations) plus classified-ask + length window for grounded rewrites |
| `recall_writing_style` | Read consolidated voice rulebook from markdown memory |
| `learn_style_from_passage` | Append a structured fingerprint from a kept passage |
| `consolidate_writing_style` | Distill fingerprints into a rulebook |

Plus everything the nuvel scaffold ships: `save_memory`/`recall_memory`, `cronjob`, `read_soul`/`update_soul`, `author_skill`, and the Composio MCP toolset.

## Skills the agent reads at runtime

- `voice-matching` — drafting in user voice. Load style first, mirror surrounding paragraphs' register, preserve user terminology, output plain text the add-in inserts cleanly.
- `rewrite-rubric` — honor the classified ask, stay within ±20% of length (unless the user said grow or shrink), preserve quotes and citations verbatim, preserve technical terminology from the surrounding document.
- `style-learning-loop` — collect kept passages → consolidate rulebook → fold in edits.

`LazySkillToolset` reloads them on mtime change — edit a SKILL.md and it takes effect on the next agent invocation, no process restart.

## File map

```
generated-agents/word-king/
├── word_king/                        # the ADK agent package
│   ├── agent.py
│   ├── prompt/instructions.py        # Word-specific DRAFT/REWRITE frame
│   ├── tools/
│   │   ├── word_context.py           # NEW — selection + full document
│   │   ├── style_tools.py            # NEW — writing-style memory
│   │   ├── draft_tools.py            # NEW — outline + rewrite hints
│   │   ├── composio_mcp.py
│   │   ├── memory_tools.py
│   │   ├── soul_tools.py / skill_tools.py / awakening_tools.py
│   │   └── __init__.py               # wires all of the above
│   ├── skills/
│   │   ├── voice-matching/SKILL.md
│   │   ├── rewrite-rubric/SKILL.md
│   │   └── style-learning-loop/SKILL.md
│   └── soul/
│       ├── SOUL.md                   # word-king character
│       └── AWAKENING.md
├── backend/                          # NEW — FastAPI bridge
│   ├── main.py
│   └── README.md
├── addin/                            # NEW — Word add-in (Office.js + React)
│   ├── manifest.xml
│   ├── package.json
│   ├── webpack.config.js
│   └── src/
│       ├── taskpane/                 # chat + context strip + insert/replace
│       ├── commands/                 # "Rewrite selection" ribbon action
│       └── config/api.ts
├── tests/
│   ├── test_word_tools.py            # NEW
│   ├── test_style_tools.py           # NEW
│   └── test_agent.py
├── memory/                           # markdown memory
├── requirements.txt
└── README.md (this file)
```

## Where to look first

- Change how it drafts → `skills/voice-matching/SKILL.md` + `memory/topics/writing-style.md` (regenerated by the learning loop).
- Change how it rewrites → `skills/rewrite-rubric/SKILL.md` + `tools/draft_tools.py`.
- Change the UI → `addin/src/taskpane/components/App.tsx`.
- Change the ribbon rewrite button → `addin/src/commands/commands.ts`.
- Change the API → `backend/main.py`.

## What's not in v1

- **OOXML-aware rewrites.** Today the add-in exchanges plain text via `Word.run` + `getSelection().text` / `insertText`. Inline formatting (bold, hyperlinks, comments, tracked changes) on the selection round-trips as plain text and is lost. To preserve them, swap `selection.text` for OOXML reads via `getOoxml()` and have the agent emit OOXML fragments — guarded by a "preserve formatting" toggle.
- **Real-time selection-changed events.** Word doesn't expose a host-stable selectionChanged hook the way Outlook does for compose-body edits, so the taskpane polls every 3s. A future version can use `Word.run`'s document event subscriptions where supported.
- **Edit-delta learning.** The learning loop fires on insert/accept; it doesn't yet diff the user's post-insert edits to feed back the strongest signal. Wire a follow-up snapshot ~30s after insert and POST the delta to `/api/word/learn-passage`.
- **Production hosting.** The manifest URLs point at `https://localhost:3000`. Swap to your Vercel / Azure Static Web Apps origin for production builds.
