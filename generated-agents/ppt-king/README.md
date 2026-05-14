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
| **Act in PowerPoint directly** | The agent has `queue_*` tools that push JSON action dicts into session state. After the chat turn finishes, the backend emits an `actions` SSE event with the queue and the taskpane runs each one against PowerPoint via `PowerPoint.run(...)`. Supported actions: `apply_slide`, `insert_slide`, `duplicate_slide`, `delete_slide`, `move_slide`, `set_notes`, `set_shape_text`, `replace_text` (deck-wide or per-slide find/replace), `add_text_box`, `request_refresh`. Results are surfaced inline in the chat and appended to a rolling `ppt:recent_edits` log the agent reads via `get_recent_edits`. |

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
│  • /chat/stream — SSE +      │
│    action queue              │
│  • /learn-slide — append     │
│    style fingerprint         │
│  • /record-edit — append to  │
│    recent edits log          │
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

Sideload `manifest.xml` (default) or `manifest.json` (unified Microsoft 365 manifest):

- **PowerPoint on the web** → Insert → Add-ins → Upload My Add-in → Browse to `manifest.xml`.
- **PowerPoint desktop (Win/Mac)** → Insert → Get Add-ins → My Add-ins → Upload My Add-in.
- **Microsoft 365 admin tenant** → Integrated apps → Upload custom apps.

The add-in ships **both manifest formats** pointing at the same task pane / commands / ribbon group, so you can sideload either one. The XML manifest is still the default (broadest Office build coverage); the JSON manifest is the path forward for newer Microsoft 365 / Teams-integrated tooling and event-based activation.

Switch formats from the `addin/` directory in any of these equivalent ways:

```bash
# default — uses manifest.xml
npm start

# opt in to the unified JSON manifest
OFFICE_MANIFEST=json npm start
# or
npm run start:json
```

The same `OFFICE_MANIFEST` env var works for `npm stop` and `npm run validate`. Direct scripts `start:xml` / `start:json` / `validate:xml` / `validate:json` bypass the env var entirely. Validate either manifest standalone with `npx office-addin-manifest validate addin/manifest.{xml,json}`.

Known limitations:
- The **JSON manifest** is required for the newer unified-manifest event-based activation surfaces, but is only fully supported on recent Microsoft 365 builds. Use the XML manifest if you're sideloading into older Office desktop builds, or onto PowerPoint on the web tenants that still require the XML schema.
- The two manifests share the same add-in `id` — sideload only one at a time per Office profile to avoid duplicate registrations.
- The current published `office-addin-manifest` validator lags the actual devPreview schema and will flag `actions`, `builtInGroupId`, and `overriddenByRibbonApi` as unknown properties on a *valid* unified manifest (the same errors fire against Microsoft's own canonical PowerPoint sample). The manifest still sideloads and runs in Office; ignore those three validator complaints until the validator catches up.

### JSON manifest features

The XML and JSON manifests are kept at parity for ribbon, taskpane, and ribbon commands. We investigated each PowerPoint-specific surface the unified JSON manifest exposes that the XML manifest cannot — most turn out **not to be supported for PowerPoint in the current schema**. We do not fake these.

| Feature | Status for PowerPoint | Notes |
|---|---|---|
| `extensions.autoRunEvents` (`OnDocumentOpened` / `newPresentationCreated`) | **Not supported.** The Microsoft Learn event-based activation matrix lists the unified-manifest event name for `OnDocumentOpened` on PowerPoint as *"Not yet supported"*. No JSON-manifest equivalent exists today. | Skipped. As a stand-in, the taskpane posts `POST /api/ppt/presentation-opened` once on first mount so the agent gets an early outline before the first chat turn. |
| `extensions.keyboardShortcuts` | **Not supported.** The custom keyboard shortcuts page lists only Excel and Word as supported hosts. | Skipped. Would need a host check anyway since the unified-manifest shortcut binding is rejected for `presentation` scope. |
| Context-aware ribbon contexts (slide-selection / shape-selection / spam-style) | **Not supported.** The `extensions.ribbons.contexts` enum allows only `default` for PowerPoint (`mailRead`, `mailCompose`, `meetingDetails*`, `spamReportingOverride` are Outlook-only). | Existing `"contexts": ["default"]` is already the canonical PowerPoint shape. |
| Smart Alerts (`OnMessageSend` equivalent) | **Not applicable.** Outlook-only. | Skipped. PowerPoint has no equivalent send/save gate. |
| Push-driven taskpane context refresh | **Supported via Office.js**, not via manifest. We register `Office.EventType.DocumentSelectionChanged` for slide and shape selection changes, and keep a slow (20 s) polling fallback for hosts that drop the event. | Active. |

So the only features actually wired today that the XML manifest can't drive on its own are the **two backend hooks** for early-open context — and they fire from the taskpane, gated by the JSON-manifest-only `lifetime: "short"` runtime declaration the XML profile does not use to bootstrap a long-lived chat session. Net effect: under the XML manifest, behavior is unchanged; under the JSON manifest, the agent gets a pre-first-turn outline and faster selection-change updates.

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
| `get_current_slide` | Read the user's selected slide (title, bullets, notes, layout, shape count, selected shapes with geometry) |
| `get_selected_shape` | Read the currently-selected shape(s) on the active slide |
| `get_deck_outline` | Read the whole deck outline (titles + bullet counts) |
| `get_recent_edits` | Read the rolling log of edits the agent has applied this session |
| `request_context_refresh` | Ask the taskpane to push a fresh PPT snapshot |
| `get_opened_presentation_snapshot` | Read the early outline the taskpane posted on mount (deck title, slide count, slide titles) — fills the gap left by PowerPoint's missing unified-manifest `OnDocumentOpened` event |
| `queue_apply_slide` | Replace title/bullets/notes on a slide |
| `queue_insert_slide` | Insert a new slide after a given index |
| `queue_duplicate_slide` | Clone a slide in place |
| `queue_delete_slide` | Remove a slide |
| `queue_move_slide` | Reorder one slide |
| `queue_set_notes` | Update only the speaker notes on a slide |
| `queue_set_shape_text` | Replace text inside one named shape |
| `queue_replace_text` | Deck-wide or per-slide find/replace |
| `queue_add_text_box` | Drop a freeform text box at given coordinates |
| `plan_deck_outline` | Brief → outline scaffold with intent, ratios, hints |
| `tighten_bullets_hints` | Per-bullet metrics (word count, verb-start, parallelism) |
| `analyze_deck_flow` | Structural observations across the deck |
| `suggest_reordering` | Concrete move suggestions with reasons |
| `recall_deck_style` | Read consolidated slide-style rulebook from markdown memory |
| `learn_style_from_kept_slide` | Append a structured fingerprint after a kept slide |
| `consolidate_deck_style` | Distill fingerprints into a rulebook |

Plus everything the nuvel scaffold ships: `save_memory`/`recall_memory`, `cronjob`, `read_soul`/`update_soul`, `author_skill`, and (if `COMPOSIO_API_KEY` is set) the Composio MCP toolset.

## Shared state & action model

Every turn, the taskpane snapshots PowerPoint with `snapshotCurrentContext()`
and pushes the result into ADK session state. Keys the agent's tools read:

| Key | Shape | Source |
|---|---|---|
| `ppt:current_slide` | `{ index, slide_id, title, bullets, notes, layout_name, shape_count, selected_shapes: [{ name, type, text, left, top, width, height, is_placeholder }] }` | `POST /api/ppt/context` |
| `ppt:deck_outline` | `{ slide_count, slides: [{ index, slide_id, title, bullet_count, has_notes }] }` | `POST /api/ppt/context` |
| `ppt:recent_edits` | rolling list (max 10) of `{ action, slide_index, summary, timestamp }` | `POST /api/ppt/record-edit` |
| `ppt:pending_actions` | queue of action dicts populated by `queue_*` tools and drained by the backend at the end of each turn | agent → addin |

Action flow:

1. User asks the agent to *do* something ("apply this", "move slide 7 to the end", "rename Acme to Globex everywhere").
2. The agent reads context (`get_current_slide` / `get_selected_shape` / `get_deck_outline`).
3. The agent calls one or more `queue_*` tools — each appends to `ppt:pending_actions`.
4. The streaming endpoint drains the queue when the agent turn ends and emits an `actions` SSE event.
5. The taskpane runs `executeActionQueue(actions)` against PowerPoint via Office.js.
6. Each successful action is logged via `/api/ppt/record-edit` so the agent can see it next turn through `get_recent_edits`.

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
│   ├── manifest.xml                  # TaskPaneApp, Host=Presentation (default)
│   ├── manifest.json                 # Unified M365 manifest (alt; set OFFICE_MANIFEST=json)
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
- **Multi-slide proposals via the chat buttons**: the manual "Apply" / "Insert" buttons act on a single proposed slide block. The agent can now apply multi-slide changes by queueing multiple `queue_insert_slide` actions in one turn (`actions` SSE event).
- **Undo**: `get_recent_edits` shows what was changed but there is no `queue_undo_last` tool yet — Office.js has no transactional API, so undo means computing the inverse action. Tracked as future work.
