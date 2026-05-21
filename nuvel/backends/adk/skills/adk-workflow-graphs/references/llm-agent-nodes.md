# LLM Agent Nodes

`LlmAgent` instances can be placed directly in workflow edges. ADK 2.0
auto-wraps them as `_LlmAgentWrapper` so they compose cleanly with function
nodes — no manual unwrapping of `types.Content` parts at every boundary.

## The wrapping behavior

When you put an `LlmAgent` in `edges`, the wrapper:

- **Without `output_schema`** — extracts the final text and outputs a `str` to
  the next node's `node_input`.
- **With `output_schema=SomeModel`** — parses the response and outputs the
  parsed `dict` (or model instance) to the next node.

This is the headline change vs. ADK 1.x, where downstream code had to dig
through `event.content.parts` to get usable text.

## Minimal example

```python
from google.adk.agents import LlmAgent
from google.adk.workflow import Workflow

writer = LlmAgent(
    name="writer",
    model="gemini-2.5-flash",
    instruction="Write a short story about the input topic.",
)

reviewer = LlmAgent(
    name="reviewer",
    model="gemini-2.5-flash",
    instruction="Review the story and provide feedback.",
)

agent = Workflow(
    name="story_pipeline",
    edges=[
        ('START', writer),
        (writer, reviewer),
    ],
)
```

`writer` outputs a `str`; it becomes `node_input` for `reviewer`. No
intermediate state key, no `output_key` plumbing required.

## Using `output_schema` for structured output

```python
from pydantic import BaseModel
from google.adk.agents import LlmAgent

class Classification(BaseModel):
    intent: str
    confidence: float

classifier = LlmAgent(
    name="classifier",
    model="gemini-2.5-flash",
    instruction="Classify the user's intent.",
    output_schema=Classification,
)

@node
def route_on_intent(node_input: dict) -> str:
    # node_input is the parsed Classification dict
    return node_input["intent"]
```

## LLM agents with tools inside a workflow

Tools work normally — the wrapper handles only the *output* to downstream
nodes. The agent can call tools, iterate, and produce a final answer:

```python
sql_writer = LlmAgent(
    name="sql_writer",
    model="gemini-2.5-flash",
    instruction="Translate the question to SQL and run it.",
    tools=[execute_sql],
)

agent = Workflow(
    edges=[
        ('START', sql_writer),
        (sql_writer, format_results),  # gets a str
    ],
)
```

## Emitting routes from an LLM node

Two options:

1. **`output_schema` + a downstream function node** that returns an `Event` with
   `route=...` (clean separation of LLM and routing logic).
2. **Tell the LLM to emit a route keyword** in its output, and use a small
   function-node classifier in between (most flexible).

```python
@node
def to_route(node_input: str):
    from google.adk import Event
    key = node_input.strip().lower()
    return Event(output=node_input, route=key if key in {"sql", "chart"} else "__DEFAULT__")

edges = [
    ('START', classifier_llm),
    (classifier_llm, to_route),
    (to_route, {"sql": sql_writer, "chart": chart_maker, "__DEFAULT__": fallback}),
]
```

## `LlmAgent` with `sub_agents` inside a workflow

A coordinator `LlmAgent` (`sub_agents=[...]`) is itself a valid workflow node.
The LLM still picks among its sub-agents at runtime — workflow routing and
LLM-routed `sub_agents` compose.

```python
coordinator = LlmAgent(
    name="coordinator",
    model="gemini-2.5-pro",
    instruction="Pick the right specialist.",
    sub_agents=[sql_expert, viz_expert],
)

agent = Workflow(
    edges=[
        ('START', preprocess),
        (preprocess, coordinator),
        (coordinator, postprocess),
    ],
)
```

## What still goes through `ctx.state` / `output_key`

Edge-flow handles step-to-step values. Use `ctx.state` (and `output_key` on an
agent) when:

- A value is read by many non-adjacent nodes (request id, user profile).
- A value needs to persist across `run` calls in the same session.
- You need it visible in traces/logs without parsing edge inputs.
