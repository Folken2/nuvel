# Function Nodes

Any Python function becomes a workflow node. This is the primary way to add
deterministic, non-LLM steps (transforms, validation, branch logic, side
effects) inside a `Workflow` graph.

## Two declaration styles

### `@node` decorator — implicit

```python
from google.adk.workflow import node

@node
def to_upper(node_input: str) -> str:
    return node_input.upper()
```

The decorator turns the function into a node usable directly in `edges`. The
node's name defaults to the function name.

### `FunctionNode` — explicit

```python
from google.adk.workflow import FunctionNode

clean = FunctionNode(lambda x: x.strip(), name="clean")
```

Use this when you want to name the node differently from the function (helpful
for tracing/debugging), reuse the same callable under different names, or wrap
a callable you don't own.

## Parameter resolution

Function-node parameters are auto-resolved by name:

- `node_input` — the upstream output (the value that flowed in along the edge).
- Any other named parameter — looked up in `ctx.state` first, then in workflow
  context.

```python
@node
def process_order(node_input: dict, user_name: str) -> str:
    # user_name is auto-resolved from ctx.state["user_name"]
    return f"{user_name}: {node_input['item']} ordered"
```

This means a node can ask for cross-cutting values (user id, request id,
config) without you having to thread them through every upstream output.

## Async, generators, type conversion

Function nodes support:

- `async def` — `await`-able dependencies inside the node.
- generators / async generators — for streaming or multi-event nodes.
- automatic type conversion when the upstream output is a Pydantic model and
  the node parameter is typed.

```python
@node
async def fetch_then_format(node_input: str, http_client) -> str:
    resp = await http_client.get(node_input)
    return resp.text
```

## When to use a function node vs. a tool

| Use a function node when | Use a tool when |
|---|---|
| The step is part of the **graph itself** — deterministic, always runs in this position | The step is **discretionary** — the LLM decides whether and when to call it |
| You want explicit input/output flow along edges | You want the LLM to pick arguments and interpret the result |
| Pure transforms, validators, formatters, joins | External API calls, search, file operations the agent invokes on demand |

A common pattern: an `LlmAgent` decides *what* to do and emits a route; a
`FunctionNode` downstream does the deterministic post-processing.

## Returning routes (preview)

A function node can emit a routing signal by returning an `Event`:

```python
from google.adk import Event

@node
def classify(node_input: str):
    return Event(output=node_input, route="error" if "fail" in node_input else "ok")
```

See `routing-and-conditions.md` for the full routing API.
