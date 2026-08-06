# Hook deeper patterns

Supporting detail for the "Common patterns" pointer in `SKILL.md`. Each section below
expands on something the main skill body already establishes — the `HookMatcher`
shape, the `ClaudeAgentOptions(hooks={...})` wiring, the `PreToolUse` / `PostToolUse` /
`UserPromptSubmit` event names, and the `(input_data, tool_use_id, context)` hook
function signature.

## Stacked hooks

A single event can have multiple `HookMatcher` entries, each pairing a different
regex `matcher` with its own handler list. The main skill's auditing example already
shows this shape for two different events (`PreToolUse` and `PostToolUse`); the same
stacking applies within one event too:

```python
options = ClaudeAgentOptions(hooks={
    "PreToolUse": [
        HookMatcher(matcher="Bash", hooks=[block_dangerous]),
        HookMatcher(matcher=None,   hooks=[audit_pre]),
    ],
})
```

Here, every `Bash` call is checked by `block_dangerous` first, and every tool call
(including `Bash`) is logged by `audit_pre`. Order is preserved — entries run in the
order they're listed — and, per the event-types table, first deny wins. Stacking lets
you compose narrow, single-purpose hooks (one for blocking, one for auditing, one for
rate limiting) instead of writing one large handler that does everything.

## Dynamic context injection in UserPromptSubmit

`UserPromptSubmit` fires on each user message, which makes it the place to inject
context that needs to be current at the moment the user speaks rather than baked in
at agent startup. Following the same `(input_data, tool_use_id, context) -> dict`
signature the minimal example establishes:

```python
async def inject_context(input_data, tool_use_id, context):
    return {}
```

Because this hook sees every user message before the agent acts on it, it's the
natural place for the "inject context" and "log" uses listed in the event-types
table — for example, logging what the user asked before any tool runs, so the audit
trail has the prompt even if a later `PreToolUse` hook denies the resulting tool call.

## Rate limiting

Rate limiting is listed alongside auditing and redaction as a hook-appropriate
concern in the skill's opening paragraph, and it fits the same shape as
`block_dangerous`: inspect `input_data`, and return a deny decision instead of `{}`
when a limit is exceeded.

```python
async def rate_limit(input_data, tool_use_id, context):
    if over_limit(input_data["tool_name"]):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "Rate limit exceeded for this tool.",
            }
        }
    return {}
```

This sits "below tool implementation," as the opening paragraph puts it: the tool
itself doesn't need to know about limits, because the hook enforces them before the
tool ever runs. Keep the `permissionDecisionReason` short and actionable, same as the
blocking-with-a-reason section — it's surfaced to Claude, which typically backs off
and tries later rather than repeating the same call.

## Redaction in PostToolUse

`PostToolUse` fires after a tool call returns, which is why the event-types table
lists "redact" as one of its uses — the hook sees the tool's output and can act on it
before that output is audited or logged. Following the audit_post shape from the main
skill:

```python
async def redact_post(input_data, tool_use_id, context):
    logger.info("tool_call.end", extra={
        "tool_name": input_data["tool_name"],
        "tool_use_id": tool_use_id,
        # redact sensitive fields from the result before they reach the log
    })
    return {}
```

The same redaction concern applies to `UserPromptSubmit`, per the event-types table
("redact" is listed there too) — a `PostToolUse` redaction hook and a
`UserPromptSubmit` redaction hook cover the two places sensitive data is most likely to
flow through the audit trail: what a tool returned, and what the user typed.

## Hook ordering and short-circuit semantics

When multiple `HookMatcher` entries apply to the same event, they run in the order
they're listed in the `hooks` dict. For `PreToolUse` specifically, the event-types
table states the short-circuit rule directly: "first deny wins." Once one hook
returns a `permissionDecision: "deny"`, later hooks in the stack don't get to
override that decision back to allow.

Practically, this means ordering is a design choice: put narrow, cheap checks (like
`block_dangerous`'s single-pattern match) before broader, more expensive ones, since a
deny from an early hook means later hooks in the stack don't need to run at all for
that tool call. Hooks that only audit or log (returning `{}` unconditionally) are
unaffected by ordering relative to each other, since they never deny — but they should
still generally run after any blocking hooks, so you're not logging a tool call that
was ultimately denied as if it were allowed.
