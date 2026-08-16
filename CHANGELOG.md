# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
