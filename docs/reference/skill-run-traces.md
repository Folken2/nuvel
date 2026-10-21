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
    def append(self, trace: dict) -> bool: ...   # False if the run_id is a duplicate
    def query(self, plugin, skill, since, tenant) -> list[dict]: ...  # tenant None = all
```

The default `LocalTraceStore` writes one line per run to

```
{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl
```

dated by the UTC `started_at`. `_runs` cannot collide with a skill feedback directory
(skill keys start with an alphanumeric character). `--trace-store module:factory`
plugs in another store; the factory is called with no arguments.

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

- **Duplicate detection is per process.** The `run_id` check runs under an in-process
  lock, so two server processes appending to the same store can both accept the same
  `run_id`. Readers should treat `run_id` as the dedup key.
- **Directory names can collide.** `{plugin}.{skill}` is ambiguous when identifiers
  contain `.` (plugin `a.b` + skill `c` and plugin `a` + skill `b.c`), so both land in
  the same files. Records keep the full `plugin` and `skill`, and
  `LocalTraceStore.query` matches on them, so health reports never mix the two; a
  tool reading the directories directly must filter on those fields too.
- **Strict function-calling translators.** The tool's `inputSchema` keeps `format`
  and `propertyNames`, which some strict function-calling translators reject. Clients
  that need a reduced schema must strip those keywords themselves; the server still
  enforces them.
