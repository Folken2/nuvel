# Experimental

This is the only page that documents the capabilities nuvel has frozen.

## Supported

Support for v1 covers these backends, and every other page in these docs describes only them:

- `adk` (Google ADK, the default)
- `hermes` (Hermes Agent)
- `claude_agent_sdk` (Claude Agent SDK, selected with `--framework claude-agent-sdk`)

## Frozen

These capabilities are experimental and frozen since 2026-11-03:

| Capability | What it is |
|---|---|
| ACP | The Agent Client Protocol adapter: `nuvel agent create --with-acp` and `nuvel acp serve`. |
| `buzz` | The Buzz backend, in `nuvel/backends/buzz/`. The `--framework` flag no longer accepts it. |
| `anthropic_managed_agents` | The Anthropic Managed Agents backend, in `nuvel/backends/anthropic_managed_agents/`. The `--framework` flag no longer accepts it. |

## What frozen means

- **The code stays.** Nothing is being deleted by this freeze, and it may still be reachable from the source tree.
- **No new work.** Frozen capabilities receive no features and no fixes.
- **No support.** Nothing about them is promised, including that they keep working.
- **Issues will be closed.** Issues filed against a frozen capability are closed without action.

If you depend on one of these, move to a [supported backend](how-to/pick-a-backend.md) or pin the version you run today.
