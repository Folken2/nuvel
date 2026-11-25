# UI events

A UI event (`nuvel.ui_event/1`) is one JSON object per page visit on a plugin
surface, recorded through the `record_ui_event` tool of the
[Skills MCP server](cli.md#nuvel-mcp-serve). Today the only surface is the
chief-of-staff `dashboard`, a page that calls the user's Nuvel connector from
their browser. A UI event says how the page was used (which catalogued actions
happened, how often, how long the visit lasted, how soon the first action came)
and nothing about what the page showed.

It is the page-side counterpart of a [skill run trace](skill-run-traces.md), and
it shares its validator, tenancy, deduplication, relay and storage choices.

## Schema

The schema is **closed**: `additionalProperties: false` on the root and on
`plugin`, and `events` is a typed map (identifier keys, bounded integer values),
the same shape as a run trace's `steps[].counts`. There is no free-text field.

```json
{
  "schema": "nuvel.ui_event/1",
  "run_id": "5d2b8c1e-4f7a-4b3d-9e60-1a8c3f7d2e94",
  "install_id": "7b1e5d42-0a9f-4e3b-8c16-5f2a7d9e4b80",
  "plugin": {"name": "chief-of-staff", "version": "0.4.0"},
  "surface": "dashboard",
  "host": "claude-chat",
  "started_at": "2026-11-20T08:15:42Z",
  "duration_s": 312,
  "first_action_s": 9,
  "events": {"view.today": 3, "task.close": 1, "draft.save_edited": 1}
}
```

| Field | Rule |
|---|---|
| `schema` | const `nuvel.ui_event/1` |
| `run_id` | UUIDv4, one per page visit. Re-sending the same `run_id` returns `duplicate`. |
| `install_id` | UUIDv4, the same random per-install pseudonym run traces use. Never derived from an email or account. |
| `plugin` | identifier (`^[a-z0-9][a-z0-9_.:-]{0,79}$`) + semver (`^\d+\.\d+\.\d+$`) |
| `surface` | enum: `dashboard` |
| `host` | enum: `claude-chat`, `claude-cowork`, `claude-code`, `claude-routine`, `codex`, `other` |
| `started_at` | RFC 3339, stored in UTC **truncated to the minute** |
| `duration_s` | int 0-86400 |
| `events` | map of event id (identifier) to int 0-10000, at most 50 keys |
| `first_action_s` | optional int 0-86400: seconds from page open to the first action |
| `tenant` | **set by the server only**, from the OAuth `client_id`; rejected if a client sends it |

Every field except `first_action_s` is required. The tool takes
`{"event": <object>}` and returns `{"status": "recorded"|"duplicate", "run_id": ...}`.
A rejected event returns `isError: true` with JSON-pointer field paths only
(`/events/*`, `/run_id`), never the offending value; a key that is not itself an
identifier is reported as `*`.

## Why it is content-free by construction

The only strings in a valid event are enums, UUIDs, a semver, a timestamp and
identifiers of at most 80 lowercase `[a-z0-9_.:-]` characters, with no spaces and
no `@`. An email address, a subject line or a person's name with a space cannot
validate, as a value or as an `events` key. The numbers are bounded counts and
durations; `started_at` is cut to the minute, so a visit cannot be tied to the
exact second a message arrived.

Residual risk: a short single-token key could still encode something (for example
`task.close.acme`). Mitigations: the plugin publishes its event catalog and keys
come only from it (the tool description says so), the 50-key cap bounds how much
a key list can carry, and a reader can flag keys outside the catalog.

## Storage

The server normalises the event (`started_at` to the minute in UTC, `tenant`
stamped) and stores it where run traces go:

- With `NUVEL_MCP_DATABASE_URL` and the Postgres trace store, the `ui_events`
  table (migration `0004_ui_events`, row-level security forced like every other
  table), unique per `(tenant, run_id)`.
- Otherwise, JSONL at `{feedback_dir}/_ui_events/{plugin}.{surface}/{YYYY-MM-DD}.jsonl`,
  with the same per-day-file `(tenant, run_id)` dedupe and locking as
  [run traces](skill-run-traces.md#duplicates). A custom
  `--trace-store module:factory` keeps UI events in these files.

`nuvel-mcp db import` copies the `_ui_events/` files into Postgres. With
`NUVEL_FEEDBACK_URL` set, each newly recorded event is also relayed as
`{"type": "ui_event", "record": ...}`; duplicates are not relayed.

## Reading usage

`check_skill_health` with `plugin` and a surface name as `skill_name` adds a `ui`
block next to the usual `runs` block:

```json
{"skill_name": "dashboard", "plugin": "chief-of-staff"}
```

```json
"ui": {"last_30_days": {"visits": 2,
                        "events": {"draft.save_edited": 1, "task.close": 1, "view.today": 4}}}
```

`visits` counts the page visits started in the last 30 days, and `events` sums
each event id over them. Over HTTP the summary is scoped to the caller's tenant,
as the `runs` block is; over stdio or with `--no-auth` it covers every tenant.
