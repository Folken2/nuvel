# Migrating Sequential / Parallel / Loop → Workflow + Task Modes

`SequentialAgent`, `ParallelAgent`, and `LoopAgent` still ship in ADK 2.0 as
convenience wrappers, but `Workflow` subsumes all three and is the idiomatic
target for anything that isn't trivially linear. Migrating buys you
conditional routing, cycles with real exit conditions, mixed Python/LLM steps,
and — with `mode` + `output_schema` — typed handoffs between steps.

Keep the shortcut classes only when the shape is a two-or-three step straight
line with no branching and you want a one-liner.

---

## The general recipe

1. Replace the container class with a `Workflow(name=..., edges=[...])`.
2. Give every `LlmAgent` step an explicit `mode`. Pipeline steps →
   `single_turn`; steps that may need the user → `task`.
3. Replace `output_key` state passing with `output_schema` + edge flow. A
   node's return value is the next node's `node_input`; only put things in
   `ctx.state` when a *later, non-adjacent* node needs them.
4. Replace "the LLM decides whether to continue" prose with a routed edge
   driven by a small `@node` function.

---

## 1. `SequentialAgent` → linear edges

**Before**

```python
from google.adk.agents import SequentialAgent

root_agent = SequentialAgent(
    name="pipeline",
    sub_agents=[extractor, transformer, reporter],
)
```

**After**

```python
from google.adk.workflow import Workflow

extractor   = LlmAgent(..., mode="single_turn", output_schema=Extracted)
transformer = LlmAgent(..., mode="single_turn", output_schema=Transformed)
reporter    = LlmAgent(..., mode="single_turn", output_schema=Report)

root_agent = Workflow(
    name="pipeline",
    edges=[
        ("START", extractor),
        (extractor, transformer),
        (transformer, reporter),
    ],
)
```

What changed in practice: each step now receives exactly its predecessor's
validated output as `node_input` instead of reading shared conversation
history. If a step used `output_key="foo"` and a later step interpolated
`{foo}` in its instruction, either keep writing `ctx.state["foo"]` in a small
function node, or pass the value along the edge if the consumer is adjacent.

---

## 2. `ParallelAgent` → fan-out tuple + fan-in

**Before**

```python
from google.adk.agents import ParallelAgent

root_agent = ParallelAgent(
    name="gather",
    sub_agents=[news_agent, filings_agent, social_agent],
)
```

**After**

```python
root_agent = Workflow(
    name="gather",
    edges=[
        ("START", (news_agent, filings_agent, social_agent)),   # fan-out
        ((news_agent, filings_agent, social_agent), synthesize),  # fan-in
    ],
)
```

Each branch is `mode='single_turn'` with its own `output_schema`.
`synthesize` receives all branch outputs. Use a `JoinNode` when you need
explicit control over aggregation; see `adk-workflow-graphs` →
`parallel-and-fanout.md`, including the rules about parallel writes to
`ctx.state`.

`ParallelAgent` had no way to route between branches. A `Workflow` does — that
is usually the reason to migrate.

---

## 3. `LoopAgent` → routed cycle

This is the migration that actually changes semantics, for the better.
`LoopAgent` looped a fixed `max_iterations` and relied on a sub-agent setting
`escalate` to break out. A `Workflow` makes the exit condition an explicit,
inspectable edge.

**Before**

```python
from google.adk.agents import LoopAgent

root_agent = LoopAgent(
    name="refine",
    sub_agents=[drafter, critic],
    max_iterations=3,
)
```

**After**

```python
from google.adk import Event
from google.adk.workflow import Workflow, node


class Critique(BaseModel):
    verdict: Literal["accept", "revise"]
    notes: str
    round: int = 0


drafter = LlmAgent(..., mode="single_turn", output_schema=Draft)
critic  = LlmAgent(..., mode="single_turn", output_schema=Critique)


@node
def decide(node_input: Critique):
    if node_input.verdict == "accept" or node_input.round >= 3:
        return Event(output=node_input, route="done")
    return Event(output=node_input, route="revise")


root_agent = Workflow(
    name="refine",
    edges=[
        ("START", drafter),
        (drafter, critic),
        (critic, decide),
        (decide, {
            "revise": drafter,        # cycle back
            "__DEFAULT__": finalize,  # the exit branch
        }),
    ],
)
```

**Hard rule:** every cycle must contain at least one routed (dict) edge. An
unconditional cycle is rejected at graph-validation time — there would be no
way out. The iteration cap that `max_iterations` gave you for free is now your
responsibility: carry a counter in the schema (as above) or in `ctx.state`.

If the loop should pause for a human instead of a critic LLM, make the review
step `mode='task'` — it can ask the user and then `finish_task` with the
verdict.

---

## 4. Multi-agent hierarchy → task delegation

**Before** — a router `LlmAgent` with `sub_agents` that it hands off to via
`transfer_to_agent`, or worse, sub-agents wrapped in `AgentTool`:

```python
root_agent = LlmAgent(
    name="router",
    sub_agents=[billing_agent, support_agent],
    tools=[AgentTool(agent=lookup_agent)],   # discouraged in 2.0
)
```

**After**

```python
billing_agent = LlmAgent(..., mode="chat")            # user is handed over
support_agent = LlmAgent(..., mode="chat")
lookup_agent  = LlmAgent(..., mode="single_turn",     # inline tool call
                         output_schema=LookupResult)

root_agent = LlmAgent(
    name="router",
    sub_agents=[billing_agent, support_agent, lookup_agent],
)
```

Dropping `AgentTool` is the point: mode-wired sub-agents run inline in the
parent's session, so events, artifacts, state, and the plugin chain stay in
one place instead of being split across a second runner.

---

## Migration checklist

- [ ] Container class replaced with `Workflow(name=..., edges=[...])`.
- [ ] Every `LlmAgent` step has an explicit `mode`.
- [ ] Every step that hands data onward has an `output_schema`.
- [ ] `output_key` / `{placeholder}` state passing reviewed — edge flow where
      adjacent, `ctx.state` where not.
- [ ] Every cycle has a routed edge and an explicit iteration cap.
- [ ] Routed edges have a `__DEFAULT__` branch.
- [ ] No remaining `AgentTool` wrappers around your own sub-agents.
- [ ] `run_adk.py` (or whatever builds the server) doesn't assume the root
      agent is an `LlmAgent` — a `Workflow` root has no `.instruction` or
      `.tools`.
