---
name: managed-agents-events
description: The event stream model for Anthropic Managed Agents — stream-first ordering, the idle-break gate, lossless reconnect via consolidation, custom-tool round-trip, and the most-common event types. Read when implementing or debugging the orchestrator loop, when sessions hang or terminate prematurely, or when reconnecting a dropped SSE stream.
---

# Driving a session through events

A Managed Agents session is an event stream. You send `user.*` events; Anthropic emits `agent.*`, `session.*`, and `span.*` events back. Get four things right and the rest follows.

## 1. Stream-first ordering

The SSE stream only delivers events that occur **after** it opens. If you send the user message and then open the stream, the agent may have already emitted its first events — they arrive buffered in a single batch and you lose real-time visibility into the early turn.

```python
# ✅ Correct
session = client.beta.sessions.create(...)
with client.beta.sessions.events.stream(session_id=session.id) as stream:
    client.beta.sessions.events.send(
        session_id=session.id,
        events=[{"type": "user.message", "content": [{"type": "text", "text": "..."}]}],
    )
    for event in stream:
        ...

# ❌ Wrong — first events arrive as one buffered batch
session = client.beta.sessions.create(...)
client.beta.sessions.events.send(...)
with client.beta.sessions.events.stream(...) as stream:  # opened too late
    for event in stream:
        ...
```

The nuvel scaffold's `orchestrator.py` does this correctly — the `with stream:` block is entered before the kickoff `events.send()`.

## 2. The idle-break gate

The naive break — `if event.type == "session.status_idle": break` — is wrong. The session goes idle transiently while waiting for:

- A `user.tool_confirmation` (when an `always_ask` permission fires)
- A `user.custom_tool_result` (when the agent calls one of your custom tools)

In both cases the session is *idle* but not *done*. The terminal vs transient distinction lives in `event.stop_reason.type`:

| `stop_reason.type` | Meaning | Action |
|---|---|---|
| `requires_action` | Waiting on you | Handle the action, **don't break** |
| `end_turn` | Normal completion | Break |
| `retries_exhausted` | Terminal failure | Break |

Plus `session.status_terminated` (terminal error or archive) — always break.

```python
for event in stream:
    handle(event)
    if event.type == "session.status_terminated":
        break
    if event.type == "session.status_idle":
        rtype = event.stop_reason.type
        if rtype != "requires_action":
            break  # end_turn or retries_exhausted — terminal
        # else: fall through, handle the pending tool/confirmation
```

Single rule: **break on terminated, or on idle with a non-`requires_action` stop reason.**

## 3. Custom-tool round-trip

When the agent calls one of your custom tools, the SDK emits `agent.custom_tool_use` and the session goes idle. Reply with `user.custom_tool_result` carrying the **event id** as `custom_tool_use_id` (not the agent's internal `toolu_...` id):

```python
if event.type == "agent.custom_tool_use":
    try:
        result = dispatch_custom_tool(event.name, event.input)
        is_error = False
    except Exception as exc:
        result = f"Tool {event.name} failed: {exc}"
        is_error = True

    client.beta.sessions.events.send(
        session_id=session.id,
        events=[{
            "type": "user.custom_tool_result",
            "custom_tool_use_id": event.id,   # NOT a toolu_ id
            "content": [{"type": "text", "text": str(result)}],
            "is_error": is_error,
        }],
    )
```

For permission prompts, the same pattern with `user.tool_confirmation` / `tool_use_id` (also `event.id`) and `result: "allow" | "deny"`.

## 4. Lossless reconnect after a dropped stream

The SSE stream has no replay. If your connection drops (network blip, deploy restart, idle timeout) and you reconnect naively, you only get events emitted *after* reconnection — anything in the gap is lost from the live stream.

The fix: on every (re)connect, fetch the full event history *before* consuming the live stream, and dedupe by event ID.

```python
seen = set()
with client.beta.sessions.events.stream(session_id=session.id) as stream:
    # 1. Drain history first — covers any gap before the stream opened.
    history = client.beta.sessions.events.list(session_id=session.id)
    for event in history.data:
        seen.add(event.id)
        handle(event)

    # 2. Tail the live stream, skipping anything we already saw.
    for event in stream:
        if event.id in seen:
            continue
        seen.add(event.id)
        handle(event)
        # ... idle-break gate ...
```

Without this, a session can deadlock: the agent emitted `agent.custom_tool_use` during your gap, the session is idle waiting on you, your reconnected stream never sees the request, and nobody resolves it.

## Event types worth knowing

| Event | What it means |
|---|---|
| `agent.message` | Text output from the agent |
| `agent.thinking` | Extended-thinking blocks (when enabled) |
| `agent.tool_use` | Prebuilt agent-toolset tool was called |
| `agent.tool_result` | Result from a prebuilt tool |
| `agent.mcp_tool_use` / `mcp_tool_result` | MCP tool call/result |
| `agent.custom_tool_use` | Your custom tool — handle host-side |
| `session.status_idle` | Agent awaiting input — check `stop_reason` |
| `session.status_running` | Active execution |
| `session.status_terminated` | Terminal — break |
| `session.error` | An error occurred (still streamed; not always terminal) |
| `span.model_request_end` | Carries `model_usage` for cost tracking |

The stream also echoes user-sent events with `processed_at: null` initially, then again with a timestamp once processed. Useful for "queued" → "acknowledged" UI states.

## A complete loop

```python
def run(client, agent_id, env_id, prompt):
    session = client.beta.sessions.create(agent=agent_id, environment_id=env_id)

    with client.beta.sessions.events.stream(session_id=session.id) as stream:
        client.beta.sessions.events.send(
            session_id=session.id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
        )

        for event in stream:
            yield event  # to caller / UI

            if event.type == "session.status_terminated":
                return

            if event.type == "session.status_idle":
                if event.stop_reason.type != "requires_action":
                    return  # terminal idle

            if event.type == "agent.custom_tool_use":
                result = dispatch_custom_tool(event.name, event.input)
                client.beta.sessions.events.send(
                    session_id=session.id,
                    events=[{
                        "type": "user.custom_tool_result",
                        "custom_tool_use_id": event.id,
                        "content": [{"type": "text", "text": str(result)}],
                    }],
                )
```

This is essentially what `orchestrator.py` does in the nuvel scaffold. Read the generated code if you need the slightly more defensive version with serialization and error handling.

## Common bugs

- **Breaking on `session.status_idle` alone** — sessions look like they "complete" right before the agent calls a tool. Always check `stop_reason.type`.
- **Sending tool results to the wrong ID** — `custom_tool_use_id` is the **event** id (e.g. `sevt_...`), not the underlying `toolu_...` id you might see embedded.
- **Stream after send** — symptoms: first 1-2 messages of every session feel "skipped" in the UI; they actually arrived in a buffered batch.
- **No reconnect plan** — works in dev, breaks the first time the dyno reboots mid-session. Always add the consolidation pattern before deploying.
