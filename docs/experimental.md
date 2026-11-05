# Removed capabilities

These capabilities were frozen on 2026-11-03 and removed on 2026-11-05. None of them was promoted back to supported.

| Capability | What was removed |
|---|---|
| ACP | The Agent Client Protocol adapter: `nuvel agent create --with-acp`, `nuvel acp serve`, the `nuvel/acp/` package and the ADK `acp` overlay. |
| `buzz` | The Buzz backend, `nuvel/backends/buzz/`. |
| `anthropic_managed_agents` | The Anthropic Managed Agents backend, `nuvel/backends/anthropic_managed_agents/`. |

The supported backends are `adk` (the default), `hermes` and `claude-agent-sdk` (selected with `--framework claude-agent-sdk`); see [pick a backend](how-to/pick-a-backend.md). If you depend on a removed capability, pin a nuvel version released before 2026-11-05.
