---
name: claude-sdk-system-prompt
description: System prompts in the Claude Agent SDK — string vs file vs preset, the claude_code preset and its append field, dynamic prompts via per-turn rebuilding, and how setting_sources composes user/project/local instructions. Read when designing the agent's voice and operating principles, when migrating an existing prompt into the SDK shape, or when choosing between a static and a dynamic prompt.
type: knowledge
---

# System prompts in the Claude Agent SDK

The SDK accepts the `system_prompt` field in three different shapes. Pick by use case, not by what looks shortest:

| Shape | Use when | Example |
|-------|----------|---------|
| **String** | The prompt is short and stable | `system_prompt="You are a helpful assistant."` |
| **`{"type": "file", "path": "..."}`** | The prompt is long and edited often | `system_prompt={"type": "file", "path": "prompt.md"}` |
| **`{"type": "preset", "preset": "claude_code", "append": "..."}`** | You want Claude Code's tool-using personality plus your domain instructions | (below) |

## File mode — the right default for production

Inline string prompts grow until they're unmaintainable. Move to a file as soon as the prompt is more than 5 lines:

```python
system_prompt={"type": "file", "path": "my_agent/prompt/system_prompt.md"}
```

Benefits:
- Edit the prompt without touching code
- `git diff` shows prompt changes clearly
- Reviewers can read it without scrolling through Python
- Markdown rendering in editors makes structure visible

The SDK reads the file every time the option is constructed, so you can edit it and restart the process. Hot-reloading mid-session requires rebuilding `ClaudeAgentOptions` and starting a fresh `ClaudeSDKClient`.

## The `claude_code` preset

```python
system_prompt={
    "type": "preset",
    "preset": "claude_code",
    "append": "You're a SQL specialist. Prefer CTEs over subqueries. Always validate schema before writing migrations.",
}
```

This gives Claude:
- The `claude_code` system prompt (proactive, tool-using, error-handling personality, autonomy norms)
- Your `append` text added at the end

Use this when your agent should *act like* Claude Code — multi-step planning, tool use without permission-asking, terse status updates. Don't use it when you want a different personality (a customer-service voice, a teaching voice, a strict review voice). The base personality is opinionated and bleeds through.

## Dynamic prompts — rebuild per turn

The SDK doesn't support "dynamic system prompt at runtime" as a first-class feature (no `InstructionProvider` like ADK). The workaround is to rebuild `ClaudeAgentOptions` and start a fresh client per turn:

```python
async def run_turn(prompt: str, user_id: str):
    options = ClaudeAgentOptions(
        system_prompt=build_dynamic_prompt(user_id),
        # ... other options ...
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            yield msg
```

You lose session continuity (each turn is a fresh session) — that's the cost of dynamic prompts. For multi-turn dynamic agents, use the SDK's `session_id` + `resume` to thread the conversation across these per-turn rebuilds.

A common middle ground: keep the base system prompt static and inject per-turn context via a `UserPromptSubmit` hook (see `claude-sdk-hooks`). Faster, simpler, no session juggling.

## `setting_sources` and how skills layer in

```python
options = ClaudeAgentOptions(
    system_prompt=...,
    setting_sources=["user", "project", "local"],
)
```

This loads `CLAUDE.md` files and `.claude/skills/` directories from those layers — exactly like Claude Code does. `user` means `~/.claude/`, `project` means the project root's `.claude/`, `local` means the current directory.

The system prompt you set via `system_prompt` is composed *with* whatever Claude finds in those sources. So you don't have to embed your skills' content in the system prompt — drop SKILL.md files into `.claude/skills/<name>/` and they're discovered.

For nuvel-scaffolded SDK agents, `setting_sources=["project"]` is the sensible default — it picks up the project's `.claude/` without leaking the user's home-directory settings into a server deployment.

## Writing a good system prompt

Three sections, in order:

1. **Identity** — who the agent is, what it's for, who's talking to it.
2. **Operating principles** — concrete behaviors. "Use tools to take action, don't ask permission." "Cite tool outputs." "Surface ambiguity in one sentence; don't block."
3. **Guardrails** — things to never do, in declarative form. "Never delete user data without explicit confirmation."

Avoid the three patterns that hurt prompts:
- **Long lists of "do this"** — Claude weights the last item. Pick 3-5 principles, ordered by importance.
- **Telling Claude its capabilities** ("you can use tools, you can read files") — the SDK already tells it. Redundant text dilutes the prompt.
- **Personality without purpose** ("you are a friendly assistant who loves to help") — describes vibes, not actions. Replace with verbs.

## Common mistakes

- **Inlining a 200-line prompt as a Python string.** Use file mode.
- **Using `claude_code` preset for non-Claude-Code agents.** The personality is too strong; users feel like they're talking to a coding agent.
- **Dynamic prompts via Python f-strings rebuilt every turn.** Works but breaks session continuity. Use `UserPromptSubmit` hook injection for per-turn context, or `session_id`/`resume` to bridge.
- **Hardcoding tool names in the prompt.** Claude already sees the tool list; mentioning specific tools in the prompt creates drift when you add/remove them.

## Quick reference

```python
# Static, file-based (recommended)
system_prompt={"type": "file", "path": "prompt/system_prompt.md"}

# Claude Code personality + your additions
system_prompt={"type": "preset", "preset": "claude_code", "append": "..."}

# With user/project/local skills auto-discovered
ClaudeAgentOptions(
    system_prompt={"type": "file", "path": "..."},
    setting_sources=["project"],
)
```
