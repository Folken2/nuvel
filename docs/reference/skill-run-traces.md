# Skill run traces

A skill run trace (`nuvel.skill_run/1`) is one JSON object per skill run, recorded
through the `record_run_trace` tool of the [Skills MCP server](cli.md#nuvel-mcp-serve).
It describes how a run went (which steps ran, which tools errored, how the work was
routed) and nothing about what the run read or wrote.

## Schema

The schema is **closed**: `additionalProperties: false` at every level. Every string
is an enum or an identifier matching `^[a-z0-9][a-z0-9_.:-]{0,79}$` (lowercase; tool
names are lowercased by the client, underscores kept, e.g. `gmail.search_threads`).
Numbers are bounded integers. There is no free-text field anywhere.

```json
{
  "schema": "nuvel.skill_run/1",
  "run_id": "3f0c2a9e-6b1d-4c8e-9a57-2d4e8f1b0c63",
  "install_id": "7b1e5d42-0a9f-4e3b-8c16-5f2a7d9e4b80",
  "plugin": {"name": "chief-of-staff", "version": "0.2.0"},
  "skill":  {"name": "radar", "version": "0.2.0"},
  "host": "claude-cowork",
  "mode": "scheduled",
  "started_at": "2026-10-20T17:43:00Z",
  "duration_s": 58,
  "outcome": "success",
  "steps": [
    {"id": "radar.1.load-state", "status": "ok"},
    {"id": "radar.2.email", "status": "ok",
     "counts": {"read": 14, "skipped_category": 6, "opened": 3, "relevant": 2}},
    {"id": "radar.3.write", "status": "ok", "counts": {"documents_created": 1}}
  ],
  "tools": [
    {"name": "gmail.search_threads", "calls": 2, "errors": 0}
  ],
  "errors": [],
  "routing": {"needs_you_now": 0, "decide": 1, "i_owe": 0, "waiting_on": 0, "watching": 2},
  "safety": {"injection_suspected": 0, "forbidden_tool_requested": 0},
  "user_corrected": false
}
```

| Field | Rule |
|---|---|
| `schema` | const `nuvel.skill_run/1` |
| `run_id`, `install_id` | UUIDv4. `install_id` is a random pseudonym generated once per install and stored by the plugin. Never derived from an email or account. |
| `plugin`, `skill` | identifier + semver (`^\d+\.\d+\.\d+$`) |
| `host` | enum: `claude-chat`, `claude-cowork`, `claude-code`, `claude-routine`, `codex`, `other` |
| `mode` | enum: `scheduled`, `interactive` |
| `started_at` | RFC 3339 UTC, **truncated to the minute** by the server |
| `duration_s` | int 0-86400 |
| `outcome` | enum: `success`, `partial`, `failure`, `blocked`, `skipped` |
| `steps[]` | at most 50. `id` is an identifier prefixed `<skill>.`. `status` enum: `ok`, `skipped`, `error`. `counts` maps identifier to int 0-100000, at most 20 keys |
| `tools[]` | at most 50. `name` is an identifier, `<connector>.<tool>`. `calls`, `errors` int 0-10000 |
| `errors[]` | at most 20. `step` identifier, `class` enum: `auth`, `rate_limit`, `not_found`, `permission`, `timeout`, `tool_error`, `contract_violation`, `registry_missing`, `other` |
| `routing` | map identifier to int (skill-defined buckets) |
| `safety` | fixed keys, ints |
| `user_corrected` | bool |
| `tenant` | **set by the server only**, from the authenticated principal; rejected if a client sends it |

Rejected traces return `isError: true` with JSON-pointer field paths only
(`/steps/2/id`), never the offending value.

## Why it is content-free by construction

The only strings in a valid trace are enums, UUIDs, semvers, timestamps, and
identifiers limited to 80 lowercase `[a-z0-9_.:-]` characters, with no spaces and no
`@`. An email address cannot validate; a subject line or a name with spaces cannot
either.

Residual risk: a short single-token identifier could still encode something (for
example a step id `radar.2.acme`). Mitigations: plugins publish their step catalog
(below) and a dashboard can flag ids that are not in it; the `record_run_trace` tool
description tells the agent that ids come only from the skill's catalog.

## Step catalogs

A plugin may ship `trace-steps.json` next to a skill:

```json
{"skill": "radar", "steps": ["radar.1.load-state", "radar.2.email"], "counts": ["read", "opened"], "routing": ["decide", "watching"]}
```

The server does not require it (it cannot see plugin files). The catalog lets a
plugin's own tests check that every step id its SKILL.md tells the agent to report is
declared, and lets a reader flag step ids outside the catalog.

## Store layout

`record_run_trace` stores the normalised trace (`started_at` truncated to the minute
in UTC, `tenant` stamped by the server) through a `TraceStore`:

```python
class TraceStore(Protocol):
    def append(self, trace: dict) -> bool: ...   # False if (tenant, run_id) is a duplicate
    def query(self, plugin, skill, since, tenant) -> list[dict]: ...  # tenant None = all
```

The default `LocalTraceStore` writes one line per run to

```
{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl
```

dated by the UTC `started_at`. `_runs` cannot collide with a skill feedback directory
(skill keys start with an alphanumeric character). `--trace-store module:factory`
plugs in another store; the factory is called with no arguments.

### Duplicates

`append` returns `False` (and `record_run_trace` answers `duplicate`) when the same
**`(tenant, run_id)`** is already in the trace's **UTC day file**. The key includes the
tenant, so another tenant sending an identical `run_id` is stored, not reported as a
duplicate (and learns nothing about the other tenant); a trace without a tenant (stdio,
`--no-auth`) is its own key. The dedupe window is that one day file: a `run_id`
resent with a `started_at` on another UTC day is stored again.

The check does not rescan the file. Each store keeps an in-memory index per day file
(the keys seen, the byte offset read up to, and the file's device, inode, ctime and
size) and, on every append, reads only the complete lines past that offset before
checking. Lines written by another process or store instance are therefore seen; a
trailing line still being written is left for the next read. The index is rebuilt from
the start if the file shrank, its device or inode changed (truncated, rotated,
replaced), or it was changed without growing or with a different line before the
offset (rewritten in place). At most 64 day files are indexed per store (least
recently used dropped, rebuilt lazily from disk), and appends are serialised by a fixed
pool of 64 striped locks, so neither grows with the number of files. Index memory is
therefore bounded by the keys in the 64 most recently used day files; the number of
keys in one day file is not capped.

## Recipe signals and shared recipes

Two further tools store recipe data next to the traces, both in the HTTP default tool
set, both tenant-stamped by the server from the OAuth `client_id` (a client-sent
`tenant` is rejected):

- **`record_recipe_signal`** stores a `nuvel.recipe_signal/1` event. Content-free:
  `signal_id`, `recipe_id`, `install_id` (UUIDs), `plugin` (`name`, `version`),
  `category` (identifier), `event` (`created`, `used`, `succeeded`, `corrected`,
  `retired`), `at`, and the cumulative counters `uses`, `successes`, `corrections`.
- **`share_recipe`** stores an anonymised `nuvel.recipe/1`: `recipe_id`,
  `install_id`, `plugin`, `version`, `category`, `language`, `title` (80 characters
  at most), `when_to_use` (200), `steps` (1 to 15 of `{action, tool_kind, text}` with
  `tool_kind` one of `email`, `calendar`, `chat`, `docs`, `storage`, `tasks`, `web`,
  `none` and `text` 160 at most) and `pitfalls` (5 at most, 160 each). The schema is
  closed. The server also screens every text field for emails, phone numbers, URLs,
  long digit runs, currency amounts and capitalised multi-word sequences, and rejects
  the whole recipe on a match, naming field paths only.

The JSON Schemas are `recipe_signal.v1.schema.json` and `recipe.v1.schema.json` in
`nuvel_mcp/schemas/`.

### Recipe store layout

The default `LocalRecipeStore` writes under `{feedback_dir}/_recipes/`:

```
_recipes/signals/{YYYY-MM-DD}.jsonl                         one signal per line
_recipes/recipes/{tenant_key}/{recipe_id}/v{version}.json   one file per recipe version
```

Records carry the server-set `tenant` when there is one, and `at` is truncated to the
UTC minute. Writers in different processes can leave duplicate signal lines, so
readers dedupe by `(tenant, signal_id)`.

Because counters are cumulative, `nuvel traces recipes` and the dashboard's
`/recipes` page take, per `(tenant, recipe_id)`, the signal with the latest `at` (the
later line wins a tie) and sum those per category. `success_rate` is
`successes / uses` (0 when there are no uses). The latest recipe shown for a category
is its most recently modified recipe file. Unreadable or malformed lines and files are
skipped, and tenant values are never displayed, only counts. Turning a candidate into
a skill is manual.

## Relay

If `NUVEL_FEEDBACK_URL` is set, the normalised trace is also POSTed as
`{"type": "skill_run", "record": {...}}` after the local append. The relay adds to
local storage, it never replaces it. A failed relay is logged (exception class and
HTTP status only, never the URL) and not retried; the local copy stands.

## Reading health

`check_skill_health(skill_name, plugin="chief-of-staff")` adds a `runs` block for
`plugin.skill` to the usual feedback report:

```json
"runs": {
  "last_30_days": {"success": 12, "partial": 1, "failure": 2, "blocked": 0, "skipped": 0},
  "errors_by_step": {"radar.2.email": {"auth": 2}},
  "total": 15
}
```

Without `plugin` the output is unchanged. The tenant is the OAuth `client_id` of the
caller's access token (never the `subject`, which is a person's login). When a tenant is
authenticated, the block counts only that tenant's runs; with no principal it covers
the whole store. That is the case over stdio, and also over HTTP with `--no-auth`
(allowed on a loopback bind only): every local caller then sees **all tenants'** runs,
and the traces it records carry no `tenant`, exactly as over stdio.

Over HTTP (`--transport http`) `plugin` is required, and the result carries only the
identifiers and the tenant-scoped `runs` block (`{"skill", "plugin", "runs"}`).
Nothing from the local feedback store is returned to remote callers, because it holds
free text (queued proposals) and other tenants' outcomes. The default HTTP tool set is
`search_skills`, `list_skills`, `list_themes`, `get_skill`, `fence_value`,
`record_run_trace` and `check_skill_health`; `record_feedback`, `propose_improvement`
and `install_skill` are exposed only when named in `--tools`.

## Known limitations

- **Duplicate detection is not atomic across processes.** The check-then-write runs
  under an in-process lock. The index picks up other processes' lines, but two server
  processes appending the same `(tenant, run_id)` at the same instant can both accept
  it. Readers should treat `(tenant, run_id)` as the dedup key.
- **An in-place rewrite within one ctime tick can go unnoticed.** The duplicate index
  trusts a day file whose ctime is unchanged since it last read it. A rewrite in place
  (for example one of the same size) that lands within the same coarse filesystem
  ctime tick as that read (some filesystems record whole seconds), and neither shrinks
  the file below the offset nor replaces it, is therefore not detected, and the index
  keeps keys the file no longer holds. Day files are append-only: editing a live day
  file by hand is unsupported.
- **The local store only grows.** There is no retention, rotation or per-tenant quota:
  every accepted trace is a line on disk forever, and one authenticated tenant can fill
  the disk. That is fine for a single-user or small
  team server; at scale, plug in a store with its own retention and quotas through
  `--trace-store module:factory` (that is the scale story, not the JSONL files).
- **Directory names can collide.** `{plugin}.{skill}` is ambiguous when identifiers
  contain `.` (plugin `a.b` + skill `c` and plugin `a` + skill `b.c`), so both land in
  the same files. Records keep the full `plugin` and `skill`, and
  `LocalTraceStore.query` matches on them, so health reports never mix the two; a
  tool reading the directories directly must filter on those fields too.
- **Strict function-calling translators.** The tool's `inputSchema` keeps `format`
  and `propertyNames`, which some strict function-calling translators reject. Clients
  that need a reduced schema must strip those keywords themselves; the server still
  enforces them.
