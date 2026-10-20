# nuvel monorepo, `nuvel-mcp` package and privacy-safe skill run traces — design

**Date:** 2026-10-20
**Status:** draft — awaiting approval
**Author:** Folken2

## Problem

nuvel now ships skills outside its own toolkit — the `chief-of-staff` plugin in
[Folken2/nuvel-plugins](https://github.com/Folken2/nuvel-plugins) runs inside
other people's Claude, Cowork and Codex sessions. To improve those skills the
author needs to see **how each run went** — which steps ran, which tools were
called, what failed, how long it took — across every install. The author must
**never** see user content: no email text, names, addresses or subjects.

Verified on 2026-10-20 (docs cited in the chief-of-staff test log):

- Nothing official gives plugin authors per-run traces. Directory analytics are
  listing views only. OpenTelemetry goes to each customer's org collector, not to
  the author, doesn't cover claude.ai chat, and replaces third-party plugin skill
  names with `third-party`.
- Plugin hooks could post tool events, but only in Cowork. Their input includes
  tool arguments, and the directory policy forbids "extraneous conversation
  data, even for logging purposes".
- A **remote MCP server** works in chat and Cowork, and probably cloud routines.
  The user connects it explicitly through OAuth, and the server defines exactly
  what it accepts.

nuvel already has the right pieces, but not in a shape that can serve this:

- `nuvel mcp serve` (the Skills MCP server, `nuvel/mcp/`) has `record_feedback`,
  `check_skill_health` and `propose_improvement` over one feedback store
  (`~/.nuvel/feedback`, PR #93). It is **stdio only**, **refuses to start
  without a skills hub** (`index.json`), and is only installable as part of
  `nuvel-cli`. `uvx --from nuvel-cli nuvel mcp serve` pulls about 98 packages
  (google-adk, litellm, fastapi, asyncpg…) for a server whose code is pure stdlib.
- It has no notion of a structured run trace. `record_feedback` is free text
  (`what_didnt`, `proposed_patch`), which is the wrong shape for content-free
  telemetry.
- `nuvel traces` / `eval` / `dashboard` read two trace schemas (ADK rich, Claude
  Agent SDK flat). They can't read skill runs.

## Goals

1. Turn `Folken2/nuvel` into a **uv workspace monorepo** that can publish more
   than one package from one repo and one version line.
2. Extract the Skills MCP server into a **lean package, `nuvel-mcp`** (stdlib
   only at its core), installable and runnable on its own
   (`uvx nuvel-mcp serve`), with `nuvel-cli` depending on it and
   `nuvel mcp serve` unchanged for existing users.
3. Add a **skill run trace** — a closed, content-free schema — and a
   `record_run_trace` MCP tool that validates and stores it in the existing
   feedback store.
4. Let the server run **without a skills hub** (feedback + traces only), which a
   hosted ingest endpoint needs.
5. Add a **Streamable HTTP transport** (optional extra) so the same server can be
   deployed remotely, with a pluggable bearer-token check.
6. Make `nuvel traces`, `nuvel eval` and `nuvel dashboard` read skill run traces
   as a third schema.

## Non-goals

- **The hosted service.** Deployment, OAuth provider, tenant model, hosted
  storage and the multi-tenant dashboard belong to a private `Folken2/nuvel-cloud`
  repo with its own spec. This spec only makes the OSS server *deployable*.
- **Plugin-side instrumentation.** Emitting traces from `chief-of-staff` is a
  separate change in `nuvel-plugins`, which consumes the schema defined here.
- Splitting other nuvel subsystems (memory, eval, bots) into packages. Only
  `nuvel-mcp` moves now; the workspace makes later moves cheap.
- Replacing `record_feedback` / `propose_improvement`. They stay; traces are a
  separate, content-free signal.
- Hook-based or OpenTelemetry-based collection (see Problem).

## Anti-goals (would be a failure even if it works)

- Any trace field that can carry user content. Privacy has to be **by
  construction** (closed schema, no free-text fields, server-side rejection),
  not by redaction or by trusting the agent.
- Breaking `nuvel mcp serve`, `from nuvel.mcp import …`, or the feedback store
  layout for existing users.
- Making `uvx nuvel-mcp serve` heavier than stdlib for the stdio path.

## Context that shaped the design

- **VISION.md:** "Adopt the commodity. Build the differentiator." The
  agent-layer trace and its privacy contract are nuvel's. Transport plumbing (MCP
  over HTTP) and trace storage/UI are commodity, so we adopt the official MCP SDK
  for HTTP rather than hand-roll it.
- **One feedback store (#93).** Traces live in the same store directory, so
  `check_skill_health` can use both signals.
- **Repo topology agreed with the user (2026-10-20):** a monorepo for code that
  shares contracts (`nuvel`, `nuvel-mcp`); separate repos for different
  audiences or cadences (`skills` hub, `nuvel-plugins` for distribution,
  private `nuvel-cloud` for the hosted deployment). No repo per service.

## Design

### 1. Workspace layout

```
Folken2/nuvel/
├── pyproject.toml              # workspace root + the nuvel-cli package (unchanged name)
├── uv.lock                     # one lock for the workspace
├── nuvel/                      # nuvel-cli code (stays where it is)
│   └── mcp/__init__.py         # compat shim: re-exports nuvel_mcp (deprecated alias)
├── packages/
│   └── nuvel-mcp/
│       ├── pyproject.toml      # name = "nuvel-mcp", deps = [] ; extras: http
│       ├── src/nuvel_mcp/
│       │   ├── server.py       # tool handlers, transport-agnostic (moved from nuvel/mcp)
│       │   ├── stdio.py        # stdlib JSON-RPC stdio loop (moved)
│       │   ├── http.py         # Streamable HTTP adapter (extra "http")
│       │   ├── feedback.py     # moved, unchanged store layout
│       │   ├── traces.py       # NEW: run-trace validation + storage
│       │   ├── schemas/        # NEW: skill_run.v1.schema.json (+ feedback schema)
│       │   ├── skills_loader.py, installer.py
│       │   └── cli.py          # `nuvel-mcp serve`
│       └── tests/
└── tests/                      # nuvel-cli tests (MCP tests move to packages/nuvel-mcp/tests)
```

My calls (a builder may revisit with reason):

- **The root stays the `nuvel-cli` package** instead of moving to
  `packages/nuvel-cli/`. This keeps the diff, the docs paths, `CLAUDE.md`, the
  template packaging and `test_packaging.py` intact. Only the MCP moves.
- **No separate `nuvel-schemas` package.** It was floated in conversation, but
  every consumer (nuvel-cli, nuvel-cloud, plugin tests) already depends on or
  can vendor `nuvel-mcp`, which is dependency-free. The schemas ship as JSON
  files inside `nuvel_mcp/schemas/` so non-Python consumers (the plugins repo's
  tests) can read them too. Split it out later if a consumer wants schemas
  without the server.
- **Lockstep versions, compatible pin.** `nuvel-cli` and `nuvel-mcp` release
  together with the same version (0.5.0 next). `nuvel-cli` requires
  `nuvel-mcp>=0.5,<0.6` — not an exact pin — so source installs between releases
  work with plain pip. uv is **not** required: `make install` becomes
  `pip install -e packages/nuvel-mcp -e .`, and `tests.yml` installs both the same
  way. (uv users get the same through the workspace.)
- **Compat shim.** `nuvel/mcp/__init__.py` re-exports the public names from
  `nuvel_mcp` and emits a `DeprecationWarning` on import. Removal: not before 0.7.
- **`--with-org-memory`** is the one heavy dependency. In `nuvel-mcp` it becomes
  a lazy import of `nuvel.memory`. When `nuvel-cli` isn't installed it fails with
  a clear message ("install nuvel-cli for --with-org-memory"); it never becomes a
  hard dependency.

### 2. Server modes

`nuvel-mcp serve` (and `nuvel mcp serve`, which delegates to it):

| Flag | Meaning |
|---|---|
| `--skills-dir PATH` | Optional now. When given, the hub tools (search/list/get/install skill…) are exposed as today. When omitted, the server runs in **feedback-only mode**: `record_feedback`, `check_skill_health`, `propose_improvement`, `record_run_trace`. |
| `--transport stdio\|http` | Default `stdio` (stdlib). `http` requires the `nuvel-mcp[http]` extra. |
| `--tools a,b,…` | Allow-list of exposed tools. Defaults differ by transport (below). |
| `--host`, `--port` | HTTP only. Default `127.0.0.1:8765`. |
| `--feedback-dir` | Unchanged (`$NUVEL_FEEDBACK_DIR`, else `~/.nuvel/feedback`). |

Behaviour change worth flagging: today a missing `index.json` is an error. In
feedback-only mode it isn't — but if `--skills-dir` **is** given and has no
index, it stays an error (so typos don't silently drop the hub tools).

### 3. Streamable HTTP transport

**Exposed tools over HTTP (my call):** by default **only `record_run_trace` and
`check_skill_health`**. A hosted endpoint must not receive free text, so
`record_feedback` and `propose_improvement` (free-text fields), `install_skill`
(writes to the server's filesystem) and GitHub issue filing (uses the server's
`GITHUB_TOKEN` on behalf of any caller) are off on HTTP unless named explicitly
in `--tools`. stdio keeps today's full tool set — it runs on the user's own
machine, where free text never leaves.

- Implemented with the **official MCP Python SDK** (`mcp` package, Streamable
  HTTP), installed only through `nuvel-mcp[http]`. The tool handlers in
  `server.py` are shared: the stdio loop and the HTTP adapter both call the same
  `handle_tools_call` / `handle_tools_list`.
- **Auth hook:** `--auth bearer` reads `$NUVEL_MCP_TOKENS` as
  `name:token,name:token` and rejects anything else with 401. The principal is
  the configured **name**, never the token. A Python entry point
  `nuvel_mcp.auth:Authenticator` (a Protocol: `authenticate(request) -> principal | None`)
  can be replaced by nuvel-cloud with its OAuth verifier. The principal becomes
  the trace's `tenant` (below). With no `--auth`, HTTP binds to localhost only, and refuses
  `--host 0.0.0.0`.
- No OAuth provider here (it lives in nuvel-cloud).

### 4. Skill run trace — `nuvel.skill_run/1`

One JSON object per skill run. **Closed schema**: `additionalProperties: false`
at every level. Every string is either an enum or an identifier matching
`^[a-z0-9][a-z0-9._:-]{0,79}$` with `_` allowed too — i.e.
`^[a-z0-9][a-z0-9_.:-]{0,79}$` (lowercase; tool names are lowercased by the
client, underscores kept: `gmail.search_threads`). Numbers are bounded integers. **There is no free-text field anywhere.**

```json
{
  "schema": "nuvel.skill_run/1",
  "run_id": "3f0c…-uuid4",
  "install_id": "7b1e…-uuid4",
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
    {"id": "radar.2.calendar", "status": "ok", "counts": {"read": 0}},
    {"id": "radar.3.write", "status": "ok", "counts": {"documents_created": 1}}
  ],
  "tools": [
    {"name": "gmail.search_threads", "calls": 2, "errors": 0},
    {"name": "gmail.get_thread", "calls": 3, "errors": 0},
    {"name": "gdrive.create_file", "calls": 1, "errors": 0}
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
| `run_id`, `install_id` | UUIDv4. `install_id` is a random pseudonym generated once per install and stored by the plugin (for chief-of-staff: in the registry `config`). Never derived from an email or account. |
| `plugin`, `skill` | identifier + semver (`^\d+\.\d+\.\d+$`) |
| `host` | enum: `claude-chat`, `claude-cowork`, `claude-code`, `claude-routine`, `codex`, `other` |
| `mode` | enum: `scheduled`, `interactive` |
| `started_at` | RFC 3339 UTC, **truncated to the minute** by the server |
| `duration_s` | int 0–86400 |
| `outcome` | enum: `success`, `partial`, `failure`, `blocked`, `skipped` |
| `steps[]` | ≤ 50. `id` identifier, prefixed `<skill>.`. `status` enum: `ok`, `skipped`, `error`. `counts` map: identifier → int 0–100000, ≤ 20 keys |
| `tools[]` | ≤ 50. `name` identifier, `<connector>.<tool>`. `calls`, `errors` int 0–10000 |
| `errors[]` | ≤ 20. `step` identifier, `class` enum: `auth`, `rate_limit`, `not_found`, `permission`, `timeout`, `tool_error`, `contract_violation`, `registry_missing`, `other` |
| `routing` | map identifier → int (skill-defined buckets) |
| `safety` | fixed keys, ints |
| `user_corrected` | bool |
| `tenant` | **set by the server only** from the authenticated principal; rejected if a client sends it |

Why this is content-free by construction: the only strings are enums,
UUIDs, semvers, timestamps and identifiers limited to 80 lowercase
`[a-z0-9_.:-]` characters, with no spaces and no `@`. An email address can't
validate; a subject line or a name with spaces can't either.
Residual risk: a short single-token identifier could still encode something
(e.g. a step id `radar.2.acme`). Mitigations: plugins publish their step catalog
(below) and the dashboard flags ids that aren't in it; the
`record_run_trace` description tells the agent that ids come only from the
skill's catalog.

**Step catalogs.** A plugin may ship `trace-steps.json` next to a skill
(`{"skill": "radar", "steps": ["radar.1.load-state", …], "counts": […], "routing": […]}`).
The server doesn't require it (it can't see plugin files). `nuvel-plugins` tests
check that every step id a SKILL.md tells the agent to report is in its catalog.

### 5. `record_run_trace` tool

- Input: one `nuvel.skill_run/1` object. Validation uses a small stdlib validator
  in `nuvel_mcp/traces.py`, driven by the JSON Schema file (no `jsonschema`
  dependency). Rejected traces return `isError: true` with the **field path
  only**, never echoing the value.
- Storage goes through a **`TraceStore` Protocol** (`append(trace)`,
  `query(plugin, skill, since, tenant)`), selected like the authenticator (entry
  point `nuvel_mcp.traces:TraceStore`), so nuvel-cloud can plug in its own. The
  default `LocalTraceStore` writes
  `{feedback_dir}/_runs/{plugin}.{skill}/{YYYY-MM-DD}.jsonl`, one line per run,
  after server-side normalisation (`started_at` truncated, `tenant` stamped).
  `_runs` can't collide with a skill directory: skill keys must start with
  `[A-Za-z0-9]`.
- Idempotent on `run_id`: a duplicate within the same day's file is ignored.
- Relay: if `$NUVEL_FEEDBACK_URL` is set, the normalised trace is **also**
  POSTed as `{"type": "skill_run", "record": …}` after the local append — the
  relay adds to local storage, it never replaces it (unlike `propose_improvement`,
  whose local queue is a fallback). A failed relay is logged and not retried; the
  local copy stands.
- `check_skill_health` gains an optional `plugin` argument. With it, it adds a
  `runs` block for `plugin.skill` (outcome counts and error classes by step over
  30 days) next to the existing feedback signals for the bare skill name;
  without it, behaviour is unchanged. Over HTTP the `runs` block is scoped to
  the caller's `tenant`; locally (no tenant) it covers the whole store.

### 6. Reading traces in nuvel-cli

- `nuvel traces` gains a third normaliser: `skill_run` lines → `Run` (agent =
  `plugin:skill`, duration, outcome, tool counts). Discovery adds
  `{feedback_dir}/_runs/**.jsonl`.
- `nuvel dashboard` shows skill runs with per-step error rates. It's the local
  single-store view; the multi-tenant view belongs to nuvel-cloud.
- `nuvel eval`: skill runs are outcome-labelled already. Scoring them is out of
  scope; they're listed so a later eval can join them with `record_feedback`
  reports by `run_id` (`correlation_id`).

### 7. Release

- `release.yml` builds and publishes both distributions (`nuvel-cli`,
  `nuvel-mcp`) with trusted publishing; `nuvel-mcp` first, since nuvel-cli pins it.
- CI runs both test suites (`uv run --package nuvel-mcp pytest packages/nuvel-mcp/tests`,
  plus the existing suite).
- Docs: CLI reference (`nuvel-mcp serve`, new flags), a "Skill run traces"
  reference page with the schema and the privacy argument, CHANGELOG, and the
  `.claude/skills/nuvel/SKILL.md` entry point (CLAUDE.md requires keeping it in
  sync with CLI flags).

## Testing

- **Packaging:** building `nuvel-mcp` yields a wheel with **zero** runtime
  dependencies; `nuvel-mcp[http]` adds only `mcp`. The existing
  `test_packaging.py` still passes for nuvel-cli.
- **Compat:** `from nuvel.mcp import SkillsMCPServer` works and warns;
  `nuvel mcp serve` with the same flags behaves as before (existing
  `test_mcp_server.py` / `test_mcp_hub_tools.py` / `test_skill_feedback_store.py`
  pass after the move).
- **Feedback-only mode:** starts without `--skills-dir`, lists exactly the four
  feedback/trace tools; `--skills-dir` pointing at a dir without `index.json`
  still errors.
- **Trace privacy (property tests):** identifiers with `_` (e.g.
  `gmail.search_threads`) validate; for random strings containing spaces,
  `@`, uppercase, non-ASCII, or length > 80, every string field rejects them;
  unknown keys at every level are rejected; a client-sent `tenant` is rejected;
  error messages never contain the rejected value.
- **Trace storage:** normalisation (minute truncation, tenant stamping),
  `run_id` idempotency, relay payload shape.
- **HTTP:** with the extra installed, an MCP client round-trips `tools/list` and
  `record_run_trace` over Streamable HTTP; `tools/list` over HTTP returns only
  the two default tools unless `--tools` widens it; `check_skill_health` over
  HTTP never returns another tenant's runs; bearer auth rejects missing or wrong
  tokens; `--host 0.0.0.0` without `--auth` refuses to start.
- **nuvel traces:** a fixture with all three schemas lists correctly.

## Delivery

Four PRs, each green and releasable on its own:

1. **Workspace + extraction** — uv workspace, move `nuvel/mcp` →
   `packages/nuvel-mcp/src/nuvel_mcp`, compat shim, `nuvel-mcp serve` entry
   point, CI and release for two packages. No behaviour change.
2. **Feedback-only mode + `record_run_trace`** — schema file, validator,
   storage, health block, docs.
3. **HTTP transport** — `[http]` extra, auth hook, docs on deploying it.
4. **nuvel-cli readers** — `nuvel traces` / `dashboard` third schema.

Then, outside this repo: `nuvel-cloud` spec (hosting, OAuth, storage, tenants)
and the `chief-of-staff` instrumentation (step catalogs, `install_id` in
config, a `## Trace` section mirrored in each run document for transparency).

## Risks

| Risk | Mitigation |
|---|---|
| The workspace move breaks editable installs or template packaging | Root package stays in place; only `nuvel/mcp` moves; PR 1 has no behaviour change and must keep `test_packaging.py` green. |
| `nuvel-cli` and `nuvel-mcp` drift | Lockstep versions + exact pin + one CI run. |
| Agents emit identifiers that encode content | Identifier regex + step catalogs + dashboard flagging; the tool description forbids non-catalog ids. |
| Official MCP SDK API churn | It's isolated in `http.py` behind the `[http]` extra; the stdio path doesn't depend on it. |
| Directory review rejects a plugin that sends traces | Opt-in by connecting the connector; the plugin README and a privacy policy declare exactly the schema above; each run document mirrors what was sent. |
