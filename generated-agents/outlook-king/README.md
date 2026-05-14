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

#### XML vs JSON manifest

Both formats ship side-by-side in `/addin`:

| File | When to use it |
|---|---|
| `manifest.xml` | Classic Outlook on Windows, Outlook on Mac, and anywhere you need the broadest compatibility. **Default.** |
| `manifest.json` | New Outlook on Windows + Outlook on the web. Unlocks the unified Microsoft 365 manifest surfaces — event-based activation (`OnNewMessageCompose`, `OnMessageSend`), Smart Alerts on-send v2, integrated spam-report, and M365 admin parity with Teams apps. |

The JSON manifest is currently only installable on new Outlook on Windows and Outlook on the web; classic Outlook on Windows + Outlook on Mac still need the XML manifest. That's why we keep both — same identity (`id`, URLs, ribbon buttons), two formats.

Switch which one `office-addin-debugging` sideloads. Two equivalent forms — explicit script, or the `OFFICE_MANIFEST` env var read by the dispatcher (`scripts/run-office-addin.js`):

```bash
# default — XML (unchanged behavior)
npm start

# JSON manifest — env-var form
OFFICE_MANIFEST=json npm start

# JSON manifest — explicit script
npm run start:json

# XML manifest, explicit
npm run start:xml
```

`stop` and `validate` accept the same `OFFICE_MANIFEST=json` env var, and the `:xml` / `:json` variants bypass it.

Validate either with:

```bash
npm run validate:xml
npm run validate:json
# or: npx office-addin-manifest validate manifest.json
```

#### JSON manifest features

The following Outlook surfaces are declared **only** in `manifest.json` —
they don't activate when the add-in is sideloaded via `manifest.xml`. The
XML build keeps its existing behavior unchanged.

| Surface | Fired on | Handler (commands.ts) | Backend route |
|---|---|---|---|
| `OnNewMessageCompose` (`newMessageComposeCreated`, Mailbox 1.10+) | New compose window opens (incl. reply/forward) | `onNewMessageComposeHandler` | `POST /api/outlook/compose-opened` |
| `OnMessageCompose` (`messageComposeOpened`, Mailbox 1.12+) | Any compose window (incl. editing a draft) | `onMessageComposeOpenedHandler` | `POST /api/outlook/compose-opened` |
| `OnMessageSend` (`messageSending`, Mailbox 1.12+, Smart Alerts `softBlock`) | User clicks Send on a message | `onMessageSendHandler` | `POST /api/outlook/pre-send-check` |
| Integrated spam reporting (`spamReportingOverride` + `spamPreProcessingDialog`, Mailbox 1.14+) | User clicks the native Report button | `onSpamReportHandler` | `POST /api/outlook/report-spam` |

Notes:

- The compose-opened events ship a draft snapshot to the backend *before*
  the task pane is even opened. The agent reads it via the new
  `get_compose_draft_snapshot` tool (state key `outlook:compose_draft`).
- Smart Alerts uses `sendMode: softBlock` — if the backend is unreachable
  or the check passes, `event.completed({allowEvent: true})` always fires.
  The current concrete check is a missing-attachment heuristic; tone /
  missing-recipient / agent-side review are stubs in `backend/main.py`.
- The spam-reporting surface logs to session state under
  `outlook:spam_reports`; agent-side triage is intentionally stubbed.
- In addition, `App.tsx` subscribes to `Office.EventType.ItemChanged` so
  the task pane refreshes context on item switch in **both** manifest
  builds (the handler API works under the XML manifest too). Only the
  manifest-declared `autoRunEvents` / spam-reporting surfaces above are
  JSON-only.

> Note: as of May 2026, `office-addin-manifest validate` emits a false-positive against `groups[].builtInGroupId` whenever a tab uses `builtInTabId` (a known quirk in how the bundled ajv evaluates the schema's `dependencies` clause — see [OfficeDev/microsoft-teams-app-schema#190](https://github.com/OfficeDev/microsoft-teams-app-schema/issues/190) and related). Our manifest does **not** set `builtInGroupId`; the structure matches what `yo office` scaffolds for Outlook. Sideloading via `npm run start:json` works regardless.

In Outlook you'll see an **outlook-king** group on the Home tab, in both read and compose modes:
- **Open outlook-king** — taskpane chat with quick actions and a context strip showing what the agent currently sees.
- **Coach my draft** — one-click voice-aware feedback while composing.

### 3. Test the heuristic tools (no LLM cost)

```bash
pytest tests/test_outlook_tools.py tests/test_style_tools.py -v
```

LLM-touching tests in `tests/test_agent.py` use ADK record/replay — see that file's docstring.

## Outlook-specific tools

### Context (read-only, pulls from session state)

| Tool | Purpose |
|---|---|
| `get_current_compose` | Compose snapshot — body, subject, to/cc/bcc, **selection inside the body**, attachments, importance, mode |
| `get_selected_message` | Read-mode snapshot — from, to/cc, body, folder, categories, flag, attachments |
| `get_outlook_account` | Account email, display name, time zone, host (Web/Desktop/Mac) |
| `get_full_outlook_state` | One-shot: compose + selected + account + recent action log |

### Actions (mutate the live mailbox via the add-in)

Each call queues an action into session state; the FastAPI bridge ships
it to the add-in over the chat response and the add-in executes against
Office.js. The outcome is recorded under `outlook:action_results`.

| Tool | Mode | What it does |
|---|---|---|
| `insert_text_at_cursor` | compose | Inserts text/HTML at caret; replaces selection if any |
| `replace_compose_body` | compose | Wipe-and-replace the entire draft |
| `set_subject` | compose | Set the subject line |
| `add_recipients` | compose | Add to/cc/bcc recipients |
| `remove_recipients` | compose | Remove specific addresses from a field |
| `set_importance` | compose | low / normal / high |
| `attach_file_from_url` | compose | Attach by URL (inline or regular) |
| `create_reply_draft` | read | Open a reply / reply-all compose pre-filled |
| `create_forward_draft` | read | Open a forward compose with recipients pre-filled |
| `apply_categories` | any | Apply Outlook categories to the current item |
| `set_flag` | read | Flag / complete / unflag the selected message |
| `refresh_outlook_context` | any | Ask the add-in to re-snapshot when state may be stale |
| `get_recent_action_results` | — | Inspect outcomes of recently-executed actions |

### Analysis & memory

| Tool | Purpose |
|---|---|
| `analyze_draft` | Objective metrics on a draft (counts, hedges, passives, structure) |
| `plan_email_search` | Natural language → structured Outlook filters |
| `rank_search_hits` | Re-rank Composio results by recency + sender weight |
| `recall_writing_style` | Read consolidated voice rulebook from markdown memory |
| `learn_style_from_sent_email` | Append a structured fingerprint after a send |
| `consolidate_writing_style` | Distill fingerprints into a rulebook |

## Shared-state model

State that flows add-in → backend → ADK session on every turn:

```
outlook:current_compose   body, body_html, subject, to/cc/bcc,
                          selection (highlighted span), selection_is_html,
                          attachments[], importance, mode, conversation_id
outlook:selected_message  id, subject, from, to, cc, body, folder,
                          categories[], flag, attachments[], received,
                          has_attachments, conversation_id
outlook:account           email, display_name, time_zone, host, platform
outlook:pending_actions   queued by action tools; drained at end of turn
outlook:action_results    rolling log of what the add-in actually executed
outlook:recent_actions    compact summary (type + status) of recent actions
```

The chat request body carries a fresh snapshot every turn; the agent's
context tools always read the latest. When the agent suspects drift
(e.g. user said "I just changed it"), it can call `refresh_outlook_context`
to ask the add-in to re-snapshot.

## Action pipeline

```
agent tool call          →  outlook:pending_actions  (session state)
end of turn              →  backend drains queue
SSE event: action {...}  →  add-in receives & executes via Office.js
add-in result            →  POST /api/outlook/action-result
                         →  outlook:action_results  (session state)
next turn                →  agent inspects via get_recent_action_results
```

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
