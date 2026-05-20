# `nuvel dashboard` — local web command center

Design spec for a browser-based view layer over the JSONL trace stream the CLI already exposes. Ships as a subcommand of the existing `nuvel` CLI; intended as a demo/portfolio surface for nuvel itself.

## Purpose

Make nuvel look impressive when shown to others. Polish, out-of-box experience, and immediate visual payoff matter more than feature breadth. A first-time visitor running `pip install nuvel-cli && nuvel dashboard --demo` should see something that looks intentional and built with care within five seconds.

The dashboard is explicitly *not* trying to be Grafana, Langfuse, or a triage console. The CLI already serves the engineer's daily-driver use cases (`nuvel traces list`, `traces show`, `traces stats`, `traces errors`). The dashboard exists because the CLI hits its ceiling on first-impressions, hover-to-explore, and "watch it happen live."

## Scope

In:
- One new CLI subcommand: `nuvel dashboard [--port] [--host] [--source] [--demo]`
- Two pages: **home** (hero + headline stats + recent runs feed) and **run detail** (header summary + thinking timeline)
- Live updates via Server-Sent Events as new trace files appear or grow
- Bundled demo fixtures loaded via `--demo` so the empty state never appears for first-time visitors
- Editorial visual style: light background, serif headlines, warm accent (orange `#c2410c`), system-font body, monospace for trace ids and event metadata

Out:
- No auth. Localhost-only.
- No write actions (no deletes, no edits, no annotation).
- No mobile-first layout. The design degrades acceptably on phones; it's optimized for laptop demos.
- No filter/search beyond the recent-activity feed on the home page.
- No persistence/database. Same JSONL files as the rest of the CLI.
- No pricing or doctor views. Those stay CLI-only.
- No additional pages, no settings, no theme switcher.

## Distribution and stack

Embedded in the `nuvel` CLI as the subcommand `nuvel dashboard`. The user types one command and the browser opens. No separate install, no `npm`, no build step at install time.

| Layer | Choice | Why |
|---|---|---|
| Server | FastAPI | Already a project dep; consistent with the meta-agent server. |
| Templating | Jinja2 | Standard with FastAPI; no client-side templating needed. |
| Interactivity | HTMX | SPA-like swaps and SSE without a JS build pipeline. |
| Styling | Tailwind CDN + small custom CSS | Polish ceiling without a node toolchain. The CDN script is fine for a localhost demo; not optimal for production sites but irrelevant here. |
| File watching | `watchfiles` | Pure-Python fallback, optional Rust accel. One new dependency. |

The dashboard is a **view layer** over `nuvel/traces_cli.py`. It does not re-implement JSONL parsing, run grouping, or any normalization. The shared loader exposes the same `Run` dataclass the CLI already produces.

## Module layout

```
nuvel/dashboard/
  __init__.py          # exports register(subparsers) for cli.py
  cli.py               # _cmd_dashboard, parser wiring, uvicorn lifecycle
  app.py               # FastAPI app factory: build_app(loader, watcher=None)
  loader.py            # TraceLoader — wraps _collect_runs from traces_cli
  watcher.py           # JSONL file watcher → SSE event stream
  fixtures/            # Bundled demo JSONL traces
    multi_agent.jsonl
    with_errors.jsonl
    cost_breakdown.jsonl
  templates/
    base.html          # Editorial frame
    home.html          # Hero + stats + errors callout + recent runs feed
    run_detail.html    # Header summary + thinking timeline
    _run_card.html     # HTMX partial: one row in the feed (reused by SSE)
    _event_row.html    # HTMX partial: one event in the timeline
  static/
    style.css          # Editorial customizations on top of Tailwind CDN
```

The `_run_card.html` and `_event_row.html` partials are reused by both the initial render and the HTMX swaps. There is one source of truth for how a run row looks.

## CLI surface

```
nuvel dashboard [--host HOST] [--port PORT] [--source DIR ...] [--demo]
```

| Flag | Default | Notes |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address. Localhost by design; opt-in to expose. |
| `--port` | `8765` | Picked deliberately (not `8000`) to avoid colliding with the meta-agent server. |
| `--source` | auto-discovery | Same semantics as `nuvel traces --source`. Repeatable. |
| `--demo` | off | Load bundled fixtures instead of doing real-trace discovery. |

Behavior on launch:
1. Resolve sources (real dirs or fixtures dir).
2. Start the FastAPI app on the requested host/port.
3. Start the watcher.
4. Open the URL in the user's default browser (via `webbrowser.open`).
5. Print the URL to the terminal so the user can re-open it manually.

## Data flow

| Request | Renders | How runs are loaded |
|---|---|---|
| `GET /` | `home.html` | `loader.collect_runs()` — server-side, fully populated on first paint. |
| `GET /run/{trace_id}` | `run_detail.html` | `loader.collect_runs(keep_events=True)`, filtered to the requested id (prefix match). |
| `GET /api/runs/feed` | `_run_card.html` × N | HTMX partial endpoint. Used by SSE swaps. |
| `GET /sse` | `text/event-stream` | Watcher channel. Emits one `event: run` message per newly-arrived run with its rendered `_run_card.html` payload. |

The home page initial paint is one HTTP request — no JS hydration step, no loading spinner. Tailwind comes from CDN; HTMX comes from CDN. The page is interactive the moment the HTML arrives.

## Watcher and SSE

`watcher.py` polls `_discover_trace_dirs()` once a second using `watchfiles.watch()`. When a JSONL file appears or grows, the watcher re-parses *just that file* via the existing `_parse_file_runs` helper, diffs the result against what it last emitted, and pushes any new `Run` objects to an `asyncio.Queue`.

The `GET /sse` endpoint consumes that queue and yields `event: run\ndata: <html>\n\n` per row. HTMX on the home page binds `hx-sse swap:run target:#feed swap:afterbegin` so new runs slot in at the top of the feed without a page refresh.

Polling at 1s, not inotify, because:
- Cross-platform behavior is consistent (no Linux/macOS divergence).
- 1s is well within HTMX's perceived-real-time threshold.
- Trace files are written by the agent process, so we're not racing the writer — we read at rest.

If `watchfiles` isn't installable in a given environment (extremely rare), the watcher gracefully degrades: it logs a warning and the SSE channel stays open but silent. The page still renders correctly on refresh.

## Demo data

Three curated JSONL fixtures live under `nuvel/dashboard/fixtures/`:

| File | Demonstrates |
|---|---|
| `multi_agent.jsonl` | Sub-agent transfers, `agent_depth`, multi-turn reasoning. Headline run for the demo. |
| `with_errors.jsonl` | `tool_exception` and `llm_error` so the errors callout has content. |
| `cost_breakdown.jsonl` | Every `llm_response` has `cost_usd`; populates the cost widget. |

The fixtures are *real* trace files generated by running real agents, then anonymized (no real PII in user inputs, no real API key fragments, no real customer names). They are committed to the repo and shipped in the wheel.

`--demo` points the loader at the fixtures dir and disables the watcher (no live events for demo data — they're static). The renderer does not know it's running on fixtures. There is no "demo mode" branch in the template logic.

## Visual design

Editorial style locked. Key choices:

| Element | Treatment |
|---|---|
| Background | `#f8f6f2` (warm off-white) |
| Foreground | `#1f1d1a` |
| Accent | `#c2410c` (warm orange), used sparingly |
| Headline font | Source Serif Pro (with Tiempos / Georgia fallback), weight 500, tight letter-spacing |
| Body font | System sans (`-apple-system, BlinkMacSystemFont, system-ui`) |
| Monospace | System ui-monospace, for trace ids, timestamps, event detail strings |
| Pills | Pill-shaped, `bg #ede9e0`, uppercase 9px, used for run status |
| Live indicator | Green pulsing dot when SSE is connected |

Two opinionated choices worth flagging:
1. The run-detail page generates a **headline sentence** describing the run (e.g., "meta_agent thought through a support agent design in seven turns"). Deterministic, rule-based, server-side. The generator picks from a small set of templates keyed off `(num_tool_calls, num_agent_transfers, has_errors)`; the verb phrase is fixed per template (e.g., "thought through", "ran into trouble while", "handed off to") with no semantic inference of the user input. Falls back to `Run {trace_id_short}` whenever the rule set doesn't match. Not LLM-driven.
2. The `llm_response` events in the timeline render the agent's `response_text` in **orange italic serif**, deliberately echoing the editorial headline style. This is the place where the visual identity does its most distinctive work — making the agent's "thinking" visible and beautiful.

The live indicator is opt-out via `--no-live` if it becomes a distraction in screenshots.

## Error handling

| Failure mode | Behavior |
|---|---|
| Malformed JSONL line | Skip silently (same policy as `_read_jsonl` in `traces_cli`). |
| Trace dir disappears mid-tick | Watcher logs a warning, continues. |
| `webbrowser.open` fails | Print URL to terminal; no error. |
| Port in use | Clear error message: "Port 8765 is in use. Try `--port`." Exit code 1. |
| No real traces and no `--demo` | Render an empty state with one-line CTA: *"Try `nuvel dashboard --demo` to see what this looks like with sample data."* |
| Unknown trace_id in `/run/{id}` | 404 with a link back to `/`. |
| `watchfiles` not importable | Log warning, run without SSE. Page still works on refresh. |

## Testing

| Module | Approach |
|---|---|
| `loader.py` | Unit tests against fixture JSONL files. Pure functions over `Path` args. |
| `app.py` | FastAPI `TestClient`: `GET /`, `GET /run/{id}` for known/unknown ids, `GET /api/runs/feed`. |
| `watcher.py` | One integration test: write a JSONL line to a tempdir, assert the SSE queue receives the new run within 2s. |
| Templates | Render snapshot in a unit test: ensures no template syntax errors and key fields appear. |
| Smoke test | `nuvel dashboard --demo --port 8766` in a subprocess, hit `/` and `/run/{id}`, assert 200 + key content. |

The watcher integration test is the only one that needs real time. Everything else is sub-second.

## Sequencing

This spec produces one plan, executed as one PR or split into 2-3 if it grows. Suggested order if split:

1. `loader.py` + `cli.py` + minimal `app.py` returning placeholder HTML. No templates yet. Verifies discovery + plumbing.
2. `home.html` + `run_detail.html` + the two partials. Editorial styling. No SSE yet. Verifies visual design.
3. `watcher.py` + SSE endpoint + live indicator. Verifies live updates.
4. `fixtures/` + `--demo` flag wiring + empty state. Verifies the demo path.

(4) is small enough to bundle into (3) if the PR doesn't grow too large.

## Open questions explicitly closed

- **"Do we need filters/search on the runs feed?"** No, not in v1. The CLI handles filtering. The feed shows the most recent N (default 20) and that's it.
- **"What about a config page for `TRACE_DIR` etc?"** No. Use `--source` or env vars.
- **"What if someone wants to deploy this remotely?"** Not v1 scope. The architecture doesn't preclude it (FastAPI + static templates), but auth, multi-tenancy, and trace ingestion from remote agents are all separate problems.
- **"Should the dashboard show pricing.json health?"** No. `nuvel doctor` does.
- **"Should we add a 'rerun this' button?"** No. No write actions in v1.

## Risks

1. **Tailwind CDN feels amateurish to some readers.** Mitigation: small `style.css` carries the editorial-specific work; Tailwind is used sparingly for utility classes. The polish is in the custom CSS, not the framework choice.
2. **`watchfiles` is one more dep.** Mitigation: optional — see Error handling. Fallback path is documented.
3. **The opinionated run-detail headline could feel cheesy if the inference is wrong.** Mitigation: keep the generator deliberately dumb and predictable; fall back to `Run {id}` whenever uncertain.
4. **SSE behind corporate proxies sometimes misbehaves.** Mitigation: localhost-only by design. Not a real risk.

## Acceptance criteria

A reasonable observer running `pip install nuvel-cli && nuvel dashboard --demo` on a fresh machine:
- Sees a polished, intentional-looking home page within five seconds.
- Can click any run row and land on a detail page that shows the agent's reasoning as a readable timeline.
- Watches a new run appear in the feed without refreshing the page (when not using `--demo`).
- Closes the laptop without remembering they were looking at a wireframe.
