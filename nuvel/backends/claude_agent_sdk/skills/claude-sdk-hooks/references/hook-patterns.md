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
order they're listed — and, per the main skill's prose just below the event-types
table ("Order is preserved; first deny wins"), first deny wins. Stacking lets
you compose narrow, single-purpose hooks (one for blocking, one for auditing, one for
rate limiting) instead of writing one large handler that does everything.

## Dynamic context injection in UserPromptSubmit

`UserPromptSubmit` fires on each user message, which makes it the place to inject
context that needs to be current at the moment the user speaks rather than baked in
at agent startup. It uses the same `(input_data, tool_use_id, context) -> dict`
signature the minimal example establishes, and the same `hookSpecificOutput` envelope
the blocking example establishes — with `additionalContext: str` in place of
`permissionDecision` (`claude_agent_sdk.types.UserPromptSubmitHookSpecificOutput`,
SDK 0.1.18). The string is prepended to the turn's context:

```python
from datetime import datetime, timezone

async def inject_context(input_data, tool_use_id, context):
    prompt = input_data["prompt"]          # UserPromptSubmitHookInput carries `prompt`
    logger.info("prompt.submit", extra={"prompt": prompt})

    lines = [f"Current UTC time: {datetime.now(timezone.utc).isoformat()}"]
    if "deploy" in prompt.lower():
        lines.append(f"Active deploy freeze: {await current_freeze_window()}")

    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n".join(lines),
        }
    }

options = ClaudeAgentOptions(hooks={
    "UserPromptSubmit": [HookMatcher(matcher=None, hooks=[inject_context])],
})
```

Two things are happening, and they're the two uses the event-types table lists for
this event. **Inject:** the freshly computed lines ride along with this turn only, so
a long-lived agent never reasons from a stale snapshot baked in at startup — which is
the whole reason to do this in a hook rather than in the system prompt. **Log:** the
hook also sees every user message before the agent acts on it, so the audit trail has
the prompt even if a later `PreToolUse` hook denies the resulting tool call.

Note `matcher=None` here: `matcher` filters on tool name, and `UserPromptSubmit`
isn't a tool event, so there is nothing to match on.

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
lists "redact" as one of its uses. The hook receives the tool's output — the
`PostToolUse` input carries `tool_response` alongside `tool_name` and `tool_input`
(`claude_agent_sdk.types.PostToolUseHookInput`, SDK 0.1.18) — so it can strip
sensitive values *before they reach your log sink*:

```python
import re

_SECRET_KEYS = {"password", "token", "api_key", "authorization", "secret"}
_SECRET_VALUE = re.compile(r"\b(sk-[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,})\b")

def _scrub(value):
    if isinstance(value, dict):
        return {
            k: "***" if k.lower() in _SECRET_KEYS else _scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("***", value)
    return value

async def redact_post(input_data, tool_use_id, context):
    logger.info("tool_call.end", extra={
        "tool_name": input_data["tool_name"],
        "tool_use_id": tool_use_id,
        "tool_response": _scrub(input_data.get("tool_response")),
    })
    return {}
```

Two things this deliberately does not do. It does not *rewrite the tool result the
model sees* — the `PostToolUse` output envelope only carries `additionalContext`
(`PostToolUseHookSpecificOutput`), so there is no field to substitute a scrubbed
result back in with; redaction here protects the audit trail, not the model's view.
And it returns `{}` unconditionally, so it can't accidentally change control flow.

The same redaction concern applies to `UserPromptSubmit`, per the event-types table
("redact" is listed there too) — a `PostToolUse` redaction hook and a
`UserPromptSubmit` redaction hook cover the two places sensitive data is most likely to
flow through the audit trail: what a tool returned, and what the user typed.

## Hook ordering and deny precedence

When multiple `HookMatcher` entries apply to the same event, they run in the order
they're listed in the `hooks` dict. The main skill states the precedence rule as prose,
not scoped to any one event: "Order is preserved; first deny wins" (`SKILL.md`, just
below the event-types table). Once one hook returns a `permissionDecision: "deny"`,
later hooks in the stack don't get to override that decision back to allow.

That is a **precedence** rule, not a short-circuit rule: nothing documented says a deny
skips the remaining hooks, so write every hook as though it will still be called after
an earlier deny. In practice that means two things. Hooks that only audit or log
(returning `{}` unconditionally) are unaffected by ordering relative to each other,
since they never deny. And a hook with side effects — writing an audit row, incrementing
a rate-limit counter — should not assume it only runs for calls that were ultimately
allowed; if that distinction matters to you, record the decision rather than inferring
it from the fact that the hook ran.

Ordering is still a design choice for cost and for reporting: put narrow, cheap checks
(like `block_dangerous`'s single-pattern match) before broader, more expensive ones, so
the first deny is the most specific reason, and put logging hooks after blocking hooks
so you're not logging a tool call that was ultimately denied as if it were allowed.
