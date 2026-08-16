---
name: claude-sdk-hooks
description: Hooks in the Claude Agent SDK — PreToolUse, PostToolUse, UserPromptSubmit, Stop event types, the HookMatcher signature, hook function signatures, blocking vs auditing patterns, and when hooks beat permission callbacks. Read when adding deterministic guards around tool calls, when implementing audit logging, when injecting context dynamically, or when can_use_tool isn't enough.
---

# Hooks in the Claude Agent SDK

Hooks are deterministic Python functions that run at fixed points in the agent's lifecycle. They're independent from `can_use_tool` (which is a single callback for permissions) — hooks are a richer system with multiple event types, multiple registered handlers per event, and the ability to inject context, rewrite inputs, or block execution.

Use hooks when you need: audit logging, deterministic safety checks, dynamic context injection, redaction before logging, or rate limiting at a layer below tool implementation.

## The minimal example

```python
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import HookMatcher

async def audit(input_data, tool_use_id, context):
    print(f"[audit] {input_data['tool_name']}({input_data['tool_input']})")
    return {}

options = ClaudeAgentOptions(
    hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[audit])]},
)
```

Three pieces:

1. **Event name** (`PreToolUse`) — the dict key in `hooks`.
2. **`HookMatcher`** — pairs a regex `matcher` with one or more hook functions. `matcher=None` means "every tool"; `matcher="Bash"` means "only Bash"; `matcher="Bash|Write"` means either.
3. **The hook function** — async, signature `(input_data, tool_use_id, context) -> dict`.

Returning `{}` is "do nothing, proceed." Other returns (below) can block, deny with a reason, or rewrite input.

## Event types

| Event | When it fires | Use for |
|-------|--------------|---------|
| `PreToolUse` | Before a tool call executes | Block, deny with reason, rewrite input, audit |
| `PostToolUse` | After a tool call returns | Audit results, redact, post-process |
| `UserPromptSubmit` | Each user message | Inject context, log, redact |
| `Stop` | Agent ends a turn | Final cleanup, persist state |
| `SubagentStop` | A subagent ends | Same as Stop, scoped to subagents |
| `PreCompact` | Before context compaction | Save important context that's about to drop |

Unlike `can_use_tool` (one callback, permissions only), each hook event can have multiple `HookMatcher` entries, each with different matchers and different handler lists. Order is preserved; first deny wins.

## Blocking with a reason

```python
async def block_dangerous(input_data, tool_use_id, context):
    if input_data["tool_name"] == "Bash":
        cmd = input_data["tool_input"].get("command", "")
        if "rm -rf" in cmd:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Blocked dangerous pattern: rm -rf",
                }
            }
    return {}
```

`permissionDecision` accepts `"allow"` (run it, even if `permission_mode` would have prompted) or `"deny"` (block it, surface the reason to Claude). `permissionDecisionReason` is shown to Claude, which usually causes it to apologize and try a different approach. Keep reasons short and actionable.

## Hooks vs `can_use_tool`

| You want… | Use hooks | Use `can_use_tool` |
|-----------|-----------|--------------------|
| Permission decisions only | ✓ (PreToolUse) | ✓ |
| Audit every tool call | ✓ | ✗ (no post-call hook) |
| Different policies per tool with regex | ✓ (`HookMatcher`) | ✗ (one global function) |
| Inject context on user messages | ✓ (UserPromptSubmit) | ✗ |
| Multiple stacked policies | ✓ (multiple matchers per event) | ✗ |

`can_use_tool` is simpler when permissions are the only concern. Hooks scale better as the agent grows. Production agents usually have hooks; toy agents usually don't.

## Auditing without blocking

The most common hook pattern: log every tool call without changing behavior.

```python
import logging
logger = logging.getLogger("audit")

async def audit_pre(input_data, tool_use_id, context):
    logger.info("tool_call.start", extra={
        "tool_name": input_data["tool_name"],
        "tool_input": input_data["tool_input"],
        "tool_use_id": tool_use_id,
    })
    return {}

async def audit_post(input_data, tool_use_id, context):
    logger.info("tool_call.end", extra={
        "tool_name": input_data["tool_name"],
        "tool_use_id": tool_use_id,
    })
    return {}

options = ClaudeAgentOptions(hooks={
    "PreToolUse":  [HookMatcher(matcher=None, hooks=[audit_pre])],
    "PostToolUse": [HookMatcher(matcher=None, hooks=[audit_post])],
})
```

Pair this with structured logging (`LOG_FORMAT=json`) and you have replayable agent traces — useful for both debugging and eval pipelines.

## Common patterns

For deeper patterns — stacked hooks, dynamic context injection in UserPromptSubmit, rate limiting, redaction in PostToolUse, hook ordering and deny precedence — see `references/hook-patterns.md`.

## Quick reference

```python
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import HookMatcher

async def my_hook(input_data, tool_use_id, context):
    return {}  # or block, or rewrite

options = ClaudeAgentOptions(
    hooks={
        "PreToolUse":  [HookMatcher(matcher="Bash", hooks=[my_hook])],
        "PostToolUse": [HookMatcher(matcher=None,   hooks=[my_hook])],
    },
)
```

Block-with-reason return:
```python
return {"hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "...",
}}
```
