# Concepts

Four ideas worth understanding before you start customizing a generated agent.

## Backends

A **backend** is the agent framework that powers the runtime: the LLM client, the agent loop, the session/state primitives, the tool-calling shape. nuvel supports three:

| Backend | Flag | What it is |
|---|---|---|
| **Google ADK** | `--framework adk` (default) | Google's open-source Agent Development Kit. Pythonic, FastAPI-friendly, mature session service. |
| **Claude Agent SDK** | `--framework claude-agent-sdk` | Anthropic's Claude Agent SDK. Streaming-first, async, uses `query()`/`receive_response()`. |
| **Anthropic Managed Agents** | `--framework anthropic-managed-agents` | Anthropic's hosted Agents API. Server-side state, Anthropic owns the runtime. |

Generated projects are **real, unwrapped framework code** — there's no nuvel runtime layer between your agent and the SDK. If you decide to leave nuvel a year from now, the agent keeps working.

For help choosing, see [Pick a backend](../how-to/pick-a-backend.md).

## Skills

A **skill** is a directory with a `SKILL.md` (frontmatter + prose) and optional helper files. Each skill teaches the agent one bounded thing — how to use a specific tool, how to handle a specific scenario, when to escalate. Skills follow the [Anthropic skills format](https://www.anthropic.com/news/skills).

Two important properties:

- **Progressive disclosure** — only the skill's `description` field is loaded into context at start. The full body is fetched on demand when the agent decides the skill is relevant. So adding 13 skills doesn't cost you 13× the context.
- **Backend-portable** — the same skill works across all three backends. The `nuvel skills list` and `nuvel skills search` commands operate per-backend because some skills are framework-specific (e.g., ADK callbacks vs. Claude SDK hooks).

Browse what's bundled:

```bash
nuvel skills list --framework adk
nuvel skills search slack --framework adk
```

## Scaffolding

`nuvel new` is a **template stamper**, not a runtime wrapper. It copies a per-backend template tree (under `nuvel/backends/<framework>/templates/`) into your filesystem, substitutes placeholders (`{{agent_name}}`, `{{agent_package}}`, etc.), and applies optional **overlays** (per flag) on top.

Each overlay lives at `nuvel/backends/adk/templates_overlays/<feature>/` and is applied by `_stamp_tree` after the base template, so later overlays can override earlier files. This is how `--with-composio`, `--persona`, and the messaging-gateway flags work — small, additive overlays composed at scaffold time.

Once stamped, your agent has no dependency on nuvel — only on its framework SDK and the libraries listed in its own `requirements.txt`.

## The meta-agent

`nuvel run` launches an **interactive ADK agent** that has read access to nuvel's own skills and source. You can chat with it to:

- Scaffold a new agent ("create me a Slack bot that summarizes Linear tickets").
- Diagnose a problem in an existing agent.
- Discover which skill or backend best fits a use case.

The meta-agent is itself just a nuvel-scaffolded ADK agent — eating its own dog food.

## Where to go next

- [Pick a backend](../how-to/pick-a-backend.md) — when each one wins.
- [Add skills to an agent](../how-to/add-skills.md) — bundling, customizing, listing.
