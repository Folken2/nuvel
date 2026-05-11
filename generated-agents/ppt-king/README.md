# ppt-king

> The agent that lives inside Microsoft PowerPoint. Outlines decks from a brief. Tightens the active slide. Tells you when the deck flows wrong. Learns your slide style over time.

Built on Google ADK with [nuvel](https://github.com/Folken2/nuvel), wired to PowerPoint through an Office.js add-in for in-app context.

## What it does

| Capability | How it works |
|---|---|
| **Outline a deck from a brief** | `plan_deck_outline` detects intent (pitch / training / report / status) from the brief and returns a scaffold with intro/body/closing ratios + intent-specific hints. The agent fills the scaffold with concrete titles, 3-5 bullets per slide (<=10 words each, parallel verb-led), and 2-4 lines of speaker notes. |
| **Tighten the active slide** | The taskpane pushes the selected slide (title, bullets, notes, layout) into session state. `tighten_bullets_hints` returns objective metrics (word count per bullet, parallelism flag, numbers, periods). The agent walks the tightening rubric — title strength, bullet count, bullet length, parallelism, notes-vs-bullets — and stops at 2-3 concrete changes. |
| **Suggest deck structure / reorder** | The taskpane pushes the deck outline. `analyze_deck_flow` surfaces evidence (repeated titles, missing agenda, bullet overload, missing CTA). `suggest_reordering` returns canonical-arc moves (agenda first, CTA last, methodology before results, problem before solution). The agent only proposes moves that earn their cost. |
| **Style-memory learning loop** | Every kept slide calls `learn_style_from_kept_slide` → appends a fingerprint (bullet count, avg/max bullet length, title length, notes presence, notes-to-bullets ratio) to the `deck-style` topic. After enough samples, the agent calls `consolidate_deck_style` to compress fingerprints into a rulebook the outlining and tightening tools apply. |

The agent's character is also self-evolving (`--persona` was on at scaffold time): SOUL.md and accumulated skills are read fresh each turn, so ppt-king genuinely sharpens over weeks of use.

## Architecture

```
┌──────────────────────────────┐
│  Microsoft PowerPoint        │
│  (Desktop / Web / Mac)       │
│  ┌────────────────────────┐  │
│  │  Add-in (Office.js)    │  │   ←── manifest.xml in /addin
│  │  • Taskpane (React)    │  │
│  │  • Tighten / Reorder   │  │
│  │    ribbon buttons      │  │
│  │  • Reads active slide  │  │
│  │    + deck outline      │  │
│  └──────┬─────────────────┘  │
└─────────┼────────────────────┘
          │ HTTPS  (POST /api/ppt/*)
          ▼
┌──────────────────────────────┐
│  Backend bridge (FastAPI)    │   ←── /backend/main.py
│  • /context  — push PPT      │
│    state into ADK session    │
│  • /chat     — run agent     │
│  • /chat/stream  — SSE       │
│  • /learn-slide — append     │
│    style fingerprint         │
└─────────┬────────────────────┘
          │
          ▼
┌──────────────────────────────┐
│  ppt-king (Google ADK)       │   ←── /ppt_king
│  • Domain tools              │
│  • Composio MCP toolset      │   ←── optional, ~1000 toolkits
│  • Skills (SKILL.md)         │
│  • Markdown memory           │
│  • Self-evolving SOUL.md     │   ←── persona overlay
└──────────────────────────────┘
```

## Running it

### 1. Agent + backend

```bash
cd generated-agents/ppt-king

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# OPENROUTER_API_KEY=...   (or GEMINI_API_KEY)
# COMPOSIO_API_KEY=...     (optional — extra toolkits)
# CORS_ORIGINS=https://localhost:3000,http://localhost:3000

uvicorn backend.main:app --reload --port 8000
```

### 2. PowerPoint add-in

```bash
cd addin
npm install
npm run dev-server
```

Sideload `manifest.xml`:

- **PowerPoint on the web** → Insert → Add-ins → Upload My Add-in → Browse to `manifest.xml`.
- **PowerPoint desktop (Win/Mac)** → Insert → Get Add-ins → My Add-ins → Upload My Add-in.
- **Microsoft 365 admin tenant** → Integrated apps → Upload custom apps.

In PowerPoint you'll see a **ppt-king** group on the Home tab with three buttons:
- **Open ppt-king** — taskpane chat with mode-aware quick actions and a context strip showing what the agent currently sees.
- **Tighten this slide** — one-click rubric run on the active slide.
- **Suggest reorder** — one-click whole-deck reorder review.

### 3. Test the heuristic tools (no LLM cost)

```bash
pytest tests/test_outline_tools.py tests/test_structure_tools.py tests/test_style_tools.py -v
```

LLM-touching tests in `tests/test_agent.py` use ADK record/replay — see that file's docstring.

## PowerPoint-specific tools

| Tool | Purpose |
|---|---|
| `get_current_slide` | Read the user's selected slide from session state |
| `get_deck_outline` | Read the whole deck outline (titles + bullet counts) |
| `plan_deck_outline` | Brief → outline scaffold with intent, ratios, hints |
| `tighten_bullets_hints` | Per-bullet metrics (word count, verb-start, parallelism) |
| `analyze_deck_flow` | Structural observations across the deck |
| `suggest_reordering` | Concrete move suggestions with reasons |
| `recall_deck_style` | Read consolidated slide-style rulebook from markdown memory |
| `learn_style_from_kept_slide` | Append a structured fingerprint after a kept slide |
| `consolidate_deck_style` | Distill fingerprints into a rulebook |

Plus everything the nuvel scaffold ships: `save_memory`/`recall_memory`, `cronjob`, `read_soul`/`update_soul`, `author_skill`, and (if `COMPOSIO_API_KEY` is set) the Composio MCP toolset.

## Skills the agent reads at runtime

- `deck-outlining` — detect intent → set ratios → draft headings → expand. Anti-patterns: too many sections, hidden CTA, missing agenda for long decks.
- `slide-tightening` — rubric for the active slide. Title strength, bullet count, bullet length, parallelism, notes-vs-bullets.
- `deck-structure` — canonical arcs by intent. Agenda first if >8 slides, CTA last, methodology before results, problem before solution.
- `style-learning-loop` — collect kept-slide fingerprints → consolidate rulebook → fold in edits.

`LazySkillToolset` reloads them on mtime change — edit a SKILL.md and it takes effect on the next agent invocation, no process restart.

## File map

```
generated-agents/ppt-king/
├── ppt_king/                         # the ADK agent package
│   ├── agent.py
│   ├── prompt/instructions.py        # ppt-specific frame
│   ├── tools/
│   │   ├── ppt_context.py            # NEW — current slide + deck outline
│   │   ├── style_tools.py            # NEW — deck-style memory
│   │   ├── outline_tools.py          # NEW — plan + bullet hints
│   │   ├── structure_tools.py        # NEW — flow + reorder
│   │   ├── composio_mcp.py
│   │   ├── memory_tools.py
│   │   ├── soul_tools.py / skill_tools.py / awakening_tools.py
│   │   └── __init__.py               # wires all of the above
│   ├── skills/
│   │   ├── deck-outlining/SKILL.md
│   │   ├── slide-tightening/SKILL.md
│   │   ├── deck-structure/SKILL.md
│   │   └── style-learning-loop/SKILL.md
│   └── soul/
│       ├── SOUL.md                   # ppt-king character
│       └── AWAKENING.md
├── backend/                          # NEW — FastAPI bridge
│   ├── main.py
│   └── README.md
├── addin/                            # NEW — PowerPoint add-in (Office.js + React)
│   ├── manifest.xml                  # TaskPaneApp, Host=Presentation
│   ├── package.json
│   ├── webpack.config.js
│   └── src/
│       ├── taskpane/                 # chat + context strip + apply/insert
│       ├── commands/                 # "Tighten" + "Reorder" ribbon actions
│       └── config/api.ts
├── tests/
│   ├── test_outline_tools.py         # NEW
│   ├── test_structure_tools.py       # NEW
│   ├── test_style_tools.py           # NEW
│   └── test_agent.py
├── memory/                           # markdown memory
├── requirements.txt
└── README.md (this file)
```

## Where to look first

- Change how it tightens → `skills/slide-tightening/SKILL.md` + `tools/outline_tools.py` (the bullet hints).
- Change how it outlines → `skills/deck-outlining/SKILL.md` + `tools/outline_tools.py` (the intent + ratios).
- Change how it restructures → `skills/deck-structure/SKILL.md` + `tools/structure_tools.py`.
- Change the UI → `addin/src/taskpane/components/App.tsx`.
- Change the API → `backend/main.py`.

## What's not in v1

- **Granular slide-change events**: PowerPoint's JS API has no clean per-shape-edit hook, so the taskpane polls every 5 s while a slide is selected. Replace with a richer event subscription when Microsoft ships it.
- **Production hosting**: the manifest URLs point at `https://localhost:3000`. Swap to your Vercel / Azure Static Web Apps origin for production builds.
- **Per-deck-type style memories**: the scaffolding is there — call `save_memory(topic="deck-type-pitch", …)` — but consolidation doesn't yet auto-segment them from the fingerprint stream.
- **Multi-slide proposals**: the taskpane's "Apply" / "Insert" buttons act on a single proposed slide block. Multi-slide outline application is a manual walk today.
