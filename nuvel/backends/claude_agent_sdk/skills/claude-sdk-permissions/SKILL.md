---
name: claude-sdk-permissions
description: The Claude Agent SDK permission model — allowed_tools, disallowed_tools, the six permission_mode values, and the can_use_tool callback. Read when designing what an agent is allowed to do, when production deploys are surprising users with permission prompts, when you want dynamic per-call gating, or when locking down a deploy that runs unattended.
type: knowledge
---

# Permissions in the Claude Agent SDK

Three layers control what Claude can do:

1. **`allowed_tools` / `disallowed_tools`** — static allowlists / blocklists by tool name.
2. **`permission_mode`** — one of six policies that govern unspecified tools.
3. **`can_use_tool`** — a callback for dynamic, per-call decisions.

Get this right once and you don't think about it again. Get it wrong and either the agent stalls on prompts you can't answer (server deploys) or runs commands you didn't intend.

## The big mental model

`allowed_tools` is **pre-approval**, not availability. Listing a tool there means "don't prompt; just run it." Tools available to Claude come from the underlying tool surface (built-in tools the SDK exposes, plus any `mcp_servers` you wire). `disallowed_tools` is the only knob that removes availability.

Two implications:

- A tool not in `allowed_tools` isn't blocked — it just triggers the permission flow set by `permission_mode`.
- A tool in `allowed_tools` that doesn't actually exist (typo, wrong server prefix) silently does nothing.

## The six permission_mode values

| Mode | Behavior for unapproved tools |
|------|-------------------------------|
| `default` | Surface a permission prompt. Right for interactive dev sessions. |
| `acceptEdits` | Auto-accept file edits (Write, Edit). Other tools still prompt. Right for "trusted" coding agents. |
| `plan` | Refuse to run any tool; Claude can still reason and write text. Right for read-only planning passes. |
| `bypassPermissions` | Run everything, no prompts. **Dangerous** — only for fully sandboxed environments. |
| `dontAsk` | Silently deny anything not in `allowed_tools`. Right for unattended server deploys. |
| `auto` | Heuristic — auto-allow read-only operations, prompt on writes/dangerous. |

## Production server deploys

Two safe configurations for a server that runs unattended:

```python
# Strict — only what you've enumerated runs.
ClaudeAgentOptions(
    permission_mode="dontAsk",
    allowed_tools=[
        "mcp__tools__lookup_user",
        "mcp__tools__send_email",
        "mcp__fs__read_file",
    ],
    disallowed_tools=["Bash"],   # belt-and-suspenders
)
```

```python
# Looser — accept edits but explicit about destructive surfaces.
ClaudeAgentOptions(
    permission_mode="acceptEdits",
    disallowed_tools=["Bash"],
    allowed_tools=["mcp__tools__safe_op"],
)
```

The second is right when the agent is editing code in a sandbox. The first is right for any agent acting on shared state (DBs, email, user data).

`permission_mode="bypassPermissions"` is rarely the right answer. The only legitimate case: the agent runs in a one-shot Docker container with no persistent state and no network access to anything that matters. If you're tempted to use it because prompts are annoying in dev, switch to `dontAsk` and explicitly list what you trust.

## The can_use_tool callback

For decisions that can't be expressed as a static list:

```python
async def can_use_tool(tool_name: str, tool_input: dict, context):
    # Block writes outside /workspace
    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "")
        if not path.startswith("/workspace/"):
            return {"behavior": "deny", "message": f"Outside workspace: {path}"}

    # Rate-limit expensive tool
    if tool_name == "mcp__tools__expensive_query":
        if rate_limiter.is_throttled():
            return {"behavior": "deny", "message": "Rate limited; try again in 30s"}

    return {"behavior": "allow", "updatedInput": tool_input}

options = ClaudeAgentOptions(can_use_tool=can_use_tool)
```

The callback can also rewrite the input via `updatedInput` — useful for redaction (strip secrets from arguments before they hit a tool) and normalization (ensure paths are absolute, etc).

`can_use_tool` runs *after* `allowed_tools` would have approved a call. If you want it to gate everything, leave `allowed_tools` empty and let the callback decide.

## Common mistakes

- **Empty `allowed_tools` with `permission_mode="default"` in a server deploy.** Claude prompts on every call; the server has no human to answer; everything stalls. Either populate `allowed_tools` or switch to `dontAsk`.
- **Forgetting the `mcp__server__` prefix.** `allowed_tools=["lookup_user"]` doesn't pre-approve `mcp__tools__lookup_user`. Use the full name.
- **Trusting `bypassPermissions` because it's convenient.** Production-grade agents start strict and relax deliberately. Easy to widen access later; hard to recover from `rm -rf` running unattended.
- **`disallowed_tools` collision with `allowed_tools`.** `disallowed_tools` wins. If you list `Bash` in both, Claude can't call it.

## Picking a default for a new agent

| Context | `permission_mode` | `allowed_tools` |
|---------|---|---|
| Interactive CLI with a human | `default` | Just the read-only tools you trust |
| Coding agent editing a sandbox | `acceptEdits` | Plus `mcp__fs__*` write tools |
| Background server with known workload | `dontAsk` | Full enumeration of permitted tools |
| Planning-only review pass | `plan` | (irrelevant — nothing runs) |

For nuvel-scaffolded server agents, the default is `acceptEdits` — change it to `dontAsk` once you know exactly which tools the agent should call.
