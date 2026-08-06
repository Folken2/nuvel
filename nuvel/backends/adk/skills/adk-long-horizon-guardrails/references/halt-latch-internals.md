# Halt latch internals

Internals for `nuvel/guardrails/halt_consumer.py` — the shared primitive every
halt guard funnels through. Read this if you're debugging a `[halted: ...]`
response that won't clear, or authoring a new guard that needs to latch
correctly.

## The state keys, exactly

ADK's `State` has no `pop`/`del`, so every "clear" operation in this module
overwrites with `None` rather than removing the key. Readers always use
`state.get(...)` and treat `None` (or any falsy value) as "absent."

| Key | Constant | Set by | Meaning when truthy |
|---|---|---|---|
| `halt_reason` | `HALT_REASON_STATE_KEY` | `latch_halt` | The latched reason string; `[halted: <reason>]` is what the model sees echoed back |
| `__halt_handoff_delivered__` | `HALT_HANDOFF_DELIVERED_STATE_KEY` | `halt_consumer_callback` | The halt envelope has been handed back to the caller at least once since the last reset |

Both are session state, so they persist across model/tool calls within a
session — a halt latched on turn 4 stays latched into turn 5, 6, 7... until
something explicitly clears it.

## Callback ordering within a turn

`GuardrailsPlugin` binds three callbacks to the standard ADK hook points, and
their order matters:

```
before_model  →  model call (or short-circuited)  →  after_model
before_tool   →  tool call                          →  after_tool
```

Concretely, in a turn where the model decides to call a tool:

1. `before_model_callback` (`halt_consumer_callback`) runs first. If a halt is
   already latched from a previous turn, it returns the `[halted: ...]`
   envelope and the model is never actually invoked this turn — nothing below
   this line runs.
2. If not halted, the model call proceeds normally.
3. `after_model_callback` (`NoProgressGuard`) inspects the response text and
   may latch a halt *for the next turn* — it does not retroactively cancel the
   response the model just gave.
4. If the model asked for a tool call, `before_tool_callback` (`exfil_guard`,
   if wired) runs before the tool executes.
5. `after_tool_callback` (`RepeatedFailureGuard`) inspects the tool result and
   may latch a halt, again for the next turn.

So a halt latched by `NoProgressGuard` or `RepeatedFailureGuard` never
interrupts the turn that triggered it — it takes effect on the *next* model
call, when `halt_consumer_callback` sees the reason already set.

## Why `latch_halt` is first-write-wins

```python
def latch_halt(state, reason):
    if state.get(HALT_REASON_STATE_KEY):
        return False           # someone already latched — this call is a no-op
    state[HALT_REASON_STATE_KEY] = reason
    return True
```

If two guards could both trip in the same turn window (say, the model loops
identically *and* a tool is also failing identically), the first one to call
`latch_halt` wins and every subsequent call is a no-op. This is deliberate:
the earliest, most specific reason is usually the actual root cause, and later
symptoms are often downstream of it. Without first-write-wins, whichever
guard's callback happened to run last would silently clobber a more
informative reason with a less informative one.

Because it's first-write-wins, a guard's own `latch_halt` call is safe to make
unconditionally once its threshold trips — it never needs to check
`state.get(HALT_REASON_STATE_KEY)` itself first.

## The handoff flag's once-per-halt semantics

`HALT_HANDOFF_DELIVERED_STATE_KEY` is not "is a halt active" — that's what
`HALT_REASON_STATE_KEY` is for. It's "has this halt already been shown to
someone." `halt_consumer_callback` sets it to `True` every time it hands back
the envelope, which for a still-latched halt is *every* model call until the
halt is acknowledged. It only starts meaning "fresh vs. already-seen" if a
wrapper checks it before the callback runs and then calls
`reset_halt_handoff` — nothing in the shipped chain does this automatically.

In practice: `acknowledge_halt(state)` (clear the reason) and
`reset_halt_handoff(state)` (clear the handoff flag) are both meant to run at
a user-turn boundary — the point where a human has actually seen the halted
response and is starting a new turn. **Neither is called anywhere in
`GuardrailsPlugin` or the generated agent's plugin chain.** If you don't wire
them into your own turn-boundary logic (e.g. in your request handler, right
before invoking the runner for a new user message), a halt latches once and
then blocks every subsequent model call in that session forever — the session
is effectively bricked. Wiring these two calls is on you; treat it as a
required part of adopting the halt guards, not an optional nicety.

When you do clear the halt, also reset the guard that tripped it:
`NoProgressGuard.reset(state)` or `RepeatedFailureGuard.reset(state)`. Without
this, a guard's internal counter is still sitting at (or above) its
threshold, so the very next identical response or failure re-trips the halt
immediately.

## Worked example: a custom budget guard

Say you want to halt a long-running agent once it's spent more than a token
or dollar budget for the session — a guard the shipped chain doesn't provide.
An `after_tool_callback` is the right hook if you're tracking, say, cumulative
cost surfaced in tool results:

```python
from nuvel.guardrails import latch_halt

_SPEND_KEY = "__budget_spend_usd__"
_BUDGET_USD = 5.00

class BudgetGuard:
    def __init__(self, *, budget_usd: float = _BUDGET_USD) -> None:
        self.budget_usd = budget_usd

    async def after_tool_callback(self, *, tool, args, tool_response, tool_context):
        cost = (tool_response or {}).get("cost_usd", 0.0) if isinstance(tool_response, dict) else 0.0
        state = tool_context.state
        spend = state.get(_SPEND_KEY, 0.0) + cost
        state[_SPEND_KEY] = spend

        if spend >= self.budget_usd:
            latch_halt(
                state,
                f"budget exceeded: spent ${spend:.2f} of ${self.budget_usd:.2f} — halting.",
            )

    @staticmethod
    def reset(state):
        """Clear the running spend. Call alongside acknowledge_halt, or the
        next tool call re-trips the halt at the same accumulated spend."""
        state[_SPEND_KEY] = 0.0
```

Three things make this latch correctly, matching the pattern every shipped
guard follows:

1. **Call `latch_halt`, don't set `HALT_REASON_STATE_KEY` directly.** That's
   what gives you first-write-wins against every other guard in the chain —
   if `RepeatedFailureGuard` already latched a reason this turn, your budget
   guard's call becomes a harmless no-op instead of overwriting it.
2. **Provide a `reset` that clears your own accumulator**, and remember to
   call it at the same place you call `acknowledge_halt` /
   `reset_halt_handoff`. Otherwise the halt re-trips on the very next
   qualifying event.
3. **Register it on `after_tool_callback`** (or whichever hook matches when
   your condition becomes observable) alongside `GuardrailsPlugin`, not
   instead of it — the shipped guards and yours all read/write the same
   `HALT_REASON_STATE_KEY`, so they compose without any extra glue.
