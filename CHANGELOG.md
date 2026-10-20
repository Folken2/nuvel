# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `nuvel-mcp` package: the Skills MCP server as its own dependency-free distribution (`uvx nuvel-mcp serve`).
- Feedback-only mode: `serve` without `--skills-dir` exposes just the feedback tools.
- `--tools A,B` allow-list of tool names to expose.
- `record_run_trace` tool: stores content-free `nuvel.skill_run/1` run traces (see `docs/reference/skill-run-traces.md`).
- `--trace-store local|MODULE:FACTORY` to choose where run traces are kept.
- `check_skill_health(plugin=...)` adds a `runs` block (30-day outcome counts and errors by step) built from run traces.
- Streamable HTTP transport for `nuvel-mcp serve` (`--transport http`, `--host`, `--port`, `--auth bearer|MODULE:FACTORY`; needs `nuvel-mcp[http]`): bearer tokens from `$NUVEL_MCP_TOKENS` stamp each trace's `tenant`, only `record_run_trace` and `check_skill_health` are exposed by default (over HTTP no tool returns feedback-store data: `check_skill_health` returns only the caller's run summary and requires `plugin`, and `get_skill` omits `health`), loopback binds validate `Host`/`Origin` against DNS rebinding, and request bodies are capped at 64 KiB. A `--auth MODULE:FACTORY` must return an object with `authenticate()`; anything else (including `None`) exits 1 instead of serving unauthenticated. Requires `mcp>=1.30`. See `docs/how-to/deploy-skills-mcp.md`.
- `nuvel traces` and `nuvel dashboard` read `nuvel.skill_run/1` run traces: skill runs appear alongside agent runs (outcome as status), and the dashboard adds a per-step "Skill steps" error table. `nuvel eval` ignores skill runs.

### Changed

- `nuvel mcp serve` without `--skills-dir` used to serve the current directory; it now starts in feedback-only mode. Migrate with `--skills-dir .`.
- The MCP `initialize` response's `serverInfo.version` is now the installed `nuvel-mcp` version instead of a fixed `1.0.0`.
- Relay failures (`propose_improvement`, `record_run_trace`) are logged as the exception class plus HTTP status only, never the message or URL, so credentials in `NUVEL_FEEDBACK_URL` cannot reach the logs.

### Deprecated

- The `nuvel.mcp` import path; import `nuvel_mcp` instead. Removal not before 0.7.

## [0.4.0] - 2026-10-18

### Added

- `nuvel.bots` module — typed Python SDK for Hermes bot lifecycle management (BotClient, SkillManager, FleetDeployer)
- `nuvel bots` CLI — create, list, chat, info, logs, delete, and send messages between bots
- `nuvel bots skills` CLI — list and search skills from the Nuvel Skills Hub
- `nuvel fleet` CLI — deploy, list, status, destroy, and update-vision for multi-bot fleet deployment
- Fleet governance: VISION.md shared constitution, Manager bot with fleet-governance skills, per-bot cron routines
- `acknowledge_halt` tool — agents can recover from halted sessions instead of bricking
- `command_guard` callback — `command_safety.classify()` now wired as a before_tool_callback in generated agents
- 7 enterprise skills in the Nuvel Skills Hub: hr/payroll-processor, customer/triage-agent, ops/inventory-checker, ops/order-processor, audit/log-inspector, fleet/manage, fleet/troubleshoot
- `validate_agent` now compiles every Python file in the agent (syntax only, nothing runs) and reports syntax errors with file and line.
- Public names for embedding nuvel's builder tools in another ADK app: `nuvel.tools.validate_tool.validate_agent_dir` and `nuvel.tools.file_tools.resolve_safe_path`. The old private names stay as aliases.
- Skills MCP server (`nuvel mcp serve`): `record_feedback` and `check_skill_health` tools over a local feedback store, with the same health report embedded in `get_skill`; `get_skill(section=...)` reads one `## ` section; `get_skill` lists `{{ var }}` template placeholders (resolved against `OrgMemoryService` with `--with-org-memory`); a `fence_value` tool for provenance-fencing values; and `--require` gating on a skill's `requires` frontmatter.

### Changed

- Skills MCP server: `propose_improvement`'s local fallback now queues into the feedback store (`--feedback-dir`, else `$NUVEL_FEEDBACK_DIR`, else `~/.nuvel/feedback`) at `feedback-queue.jsonl`, so `check_skill_health` sees queued proposals. `NUVEL_FEEDBACK_QUEUE` is no longer read.

### Fixed

- Migration header in 0002_entity_links.sql now accurately describes the 3 columns it adds (#61)
- THIRD_PARTY.md now ships in the installed wheel (PEP 639 license-files) (#62)
- PLUGIN_INSTANCES parser replaced with AST-based walker — handles .append() and += (#63)
- Added subsystem coverage guard — new packages can't ship without skill references (#64)
- `CostGuardPlugin` kept the running cost on the plugin instance, which a server shares across every session: `session_cost_usd` summed all users, and `COST_GUARD_BUDGET` blocked everyone once the server-wide total passed it. The total now lives in each session's state. Fixed in the meta-agent chain and in the generated-agent template.
- `CostGuardPlugin` and `ContextWindowPlugin` kept the model of the call in flight on the instance, so concurrent sessions could price or size a call against another session's model. The model is now keyed by invocation and agent.
- The meta-agent `CachePlugin` served `read_file`, `list_files` and `validate_agent` results from before a `write_file`, `scaffold_agent` or `install_skill` call for up to five minutes; those tools now clear the session's cache.
- A latched guardrail halt never cleared: the model is short-circuited while latched, so it could never call `acknowledge_halt`, and every later turn in the session came back `[halted: …]`. The halt now stops the turn it fired in, and the user's next message clears it and resets the guard counters. Fixed in `nuvel/guardrails/` and the generated-agent template.
- Scaffolds made from an installed nuvel (PyPI or git) had no `.env.example`, `.dockerignore`, `.gitignore` or `.gitkeep` files, because the `package-data` globs never match a leading dot. Added dotfile patterns and `tests/test_packaging.py`, which checks every template and skill file is covered.
- `plugins/context_windows.json` was missing from the wheel, so an installed `ContextWindowPlugin` had no window sizes.
- `google/gemini-3-flash-preview` had no entry in `pricing.json` or `context_windows.json`, so CostGuard never counted its cost (and a budget never triggered) and ContextWindow reported no percentages for it. Added to the meta-agent and template copies, priced from OpenRouter.

## [0.3.0] - 2026-08-06

### Added

- `--with-acp` — ACP-compatible, CLI-runnable ADK agents ([#48](https://github.com/Folken2/nuvel/pull/48)), extended to honor the editor's `mcpServers` and add an fs bridge ([#49](https://github.com/Folken2/nuvel/pull/49)).
- Long-horizon guardrails, resumability, event compaction, and memory self-improvement for generated agents ([#50](https://github.com/Folken2/nuvel/pull/50)).
- Cron isolation — scoped secrets, a headless tool-approval policy, HITL-gated job creation ([#51](https://github.com/Folken2/nuvel/pull/51)).
- `OrgMemoryService` runner wiring — auto-wired into `run_adk` via `NUVEL_ORG_MEMORY_URI` ([#54](https://github.com/Folken2/nuvel/pull/54)).
- Hybrid RRF retrieval, a zero-LLM knowledge graph, and relational recall for `OrgMemoryService` ([#55](https://github.com/Folken2/nuvel/pull/55)).
- Five new ADK knowledge skills: `adk-long-horizon-guardrails`, `adk-long-horizon-sessions`, `adk-cron-isolation`, `adk-org-memory-retrieval`, and `adk-memory-self-improvement` (marked experimental) — ADK skills 10 → 15; 26 across all three frameworks ([#56](https://github.com/Folken2/nuvel/pull/56)).
- `THIRD_PARTY.md`, crediting [`garrytan/gbrain`](https://github.com/garrytan/gbrain) (MIT, © 2026 Garry Tan) for the org-memory retrieval algorithm designs behind `nuvel/memory/hybrid.py`, `relational.py`, `extraction.py`, and `synthesis.py` — independent Python reimplementations, no source vendored.
- `tests/test_skills_integrity.py` — asserts every skill's referenced reference files exist, skill frontmatter is valid, per-framework skill counts are 15/6/5, every env var the ADK template reads is documented in `.env.example`, and every plugin in `PLUGIN_INSTANCES` is documented in the README's Plugin Chain table.

### Changed

- Documented a 3-tier cache-stable prompt contract in `adk-prompt-engineering` — prompt-prefix stability is a cost decision, previously undocumented.
- Added 18 previously-undocumented environment variables to the generated-agent `.env.example` (compaction/resumability knobs, the skill-curator family, older cache/LLM-retry/color vars).
- Corrected stale counts across `README.md`, `CLAUDE.md`, and `.claude/skills/nuvel/SKILL.md`: skill counts (previously stated as 8/6/5, actually 10 at the time, now 15/6/5), and the generated-agent plugin chain (previously stated as 11, actually 17). The meta-agent's own plugin chain (12, via `PLUGIN_FLAGS`) is now distinguished from the generated-agent chain rather than conflated with it.

### Fixed

- Two reference files promised by skills but never written: `adk-composio-tool-router/references/composio-patterns.md` and `claude-sdk-hooks/references/hook-patterns.md`.
- `adk-composio-tool-router` incorrectly stated that per-tool filtering was impossible and advised provisioning extra Composio identities as a workaround; both `McpToolset(tool_filter=...)` and `ToolRouter.create(tools=/toolkits=/tags=)` support it.

## [0.2.0] - 2026-07-23

76 commits since `v0.1.1`. Highlights, grouped by theme (not exhaustive):

### Added

- ADK 2.0 graph-based agents, plus the references-table convention for skills documenting them ([#36](https://github.com/Folken2/nuvel/pull/36), [#37](https://github.com/Folken2/nuvel/pull/37)).
- `OrgMemoryService` v1 — hierarchical, scope-aware memory ([#39](https://github.com/Folken2/nuvel/pull/39)).
- `ContextWindowPlugin` for live context-window usage tracking ([#42](https://github.com/Folken2/nuvel/pull/42)).
- `nuvel eval` — an online trace scorer ([#35](https://github.com/Folken2/nuvel/pull/35)), later extended with replay A/B (`nuvel eval replay/compare/variants`) ([#41](https://github.com/Folken2/nuvel/pull/41)).
- `nuvel dashboard` — a local Editorial-style command center ([#33](https://github.com/Folken2/nuvel/pull/33)); `nuvel traces` for cross-agent trace inspection, later extended with error-rate surfacing ([#22](https://github.com/Folken2/nuvel/pull/22), [#29](https://github.com/Folken2/nuvel/pull/29)); `nuvel pricing` to sync pricing data from OpenRouter ([#27](https://github.com/Folken2/nuvel/pull/27)); `nuvel doctor` diagnostics ([#10](https://github.com/Folken2/nuvel/pull/10)).
- Scheduled prompts (`nuvel cron`) — service, scheduler, HTTP API, `/cron` slash command ([#17](https://github.com/Folken2/nuvel/pull/17)).
- Unified `AgentHarness` with persistent versioned artifacts ([#45](https://github.com/Folken2/nuvel/pull/45)); ADK Workflows + Task API support, including a workflow-safe scaffolding path and template ([#46](https://github.com/Folken2/nuvel/pull/46)).
- Unified slash-command registry across CLI and channels ([#11](https://github.com/Folken2/nuvel/pull/11)); voice-memo transcription and `/personality` runtime overlay for gateways.
- Gateway multimodal support: inbound/outbound attachments across Slack, Telegram, and Teams, with artifact upload via Composio ([#8](https://github.com/Folken2/nuvel/pull/8)).

### Changed

- Single source of truth for default model IDs ([#32](https://github.com/Folken2/nuvel/pull/32)).

### Fixed

- Wired `artifact_service` and the plugin chain into the gateway `Runner` ([#44](https://github.com/Folken2/nuvel/pull/44)).
- Dropped duplicate `list_skills`/`read_skill` `FunctionTool`s in favor of the built-in `SkillToolset` ([#20](https://github.com/Folken2/nuvel/pull/20)).

This range also includes example-agent work on `outlook-king`/`word-king`/`ppt-king` (Office.js add-ins built on the ADK pattern) and documentation passes covering CLAUDE.md/AGENTS.md, doctor, traces, pricing, and env vars — omitted above as internal/example-scoped rather than user-facing `nuvel-cli` changes.

## [0.1.1] - 2026-05-09

### Changed

- Broadened the PyPI package description to cover all three backends (ADK, Claude Agent SDK, Managed Agents), not just the default.

## [0.1.0] - 2026-05-09

Initial release, published to PyPI as `nuvel-cli`.

[0.3.0]: https://github.com/Folken2/nuvel/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Folken2/nuvel/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Folken2/nuvel/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Folken2/nuvel/releases/tag/v0.1.0
