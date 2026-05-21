# Routing and Conditions

Conditional and cyclical edges are first-class in `Workflow`. A node emits a
**route string**, and the edge maps route strings to target nodes.

## Emitting a route

A node signals a route by returning an `Event` with `output=` and `route=`:

```python
from google.adk import Event
from google.adk.workflow import node

@node
def classify(node_input: str):
    if "error" in node_input:
        return Event(output=node_input, route="error")
    return Event(output=node_input, route="success")
```

The `output` field becomes the next node's `node_input` as usual; the `route`
field selects which edge to follow.

## Dict-syntax routing (preferred)

Map route values to target nodes in a single edge tuple:

```python
edges = [
    (classify, {
        "success": handle_success,
        "error":   handle_error,
    }),
]
```

This is the idiomatic form — the routing table sits right next to the node
that produces the routes.

## Default fallback

Use `'__DEFAULT__'` as the catch-all when no other route key matches:

```python
edges = [
    (classify, {
        "success": handle_success,
        "error":   handle_error,
        "__DEFAULT__": fallback,
    }),
]
```

If no `__DEFAULT__` is supplied and a route doesn't match, the graph compiler
or runtime will raise.

## Cyclical edges (revision loops)

Nodes can route back to themselves or earlier nodes:

```python
edges = [
    ('START', draft_email),
    (draft_email, human_review),
    (human_review, {
        "revise":   draft_email,    # back to an earlier node
        "approved": send,
        "guess_again": human_review, # self-loop
    }),
]
```

**Hard rule:** cycles must include at least one routed edge. An unconditional
cycle (`(reviewer, drafter)` with no dict) is rejected during graph validation
— there'd be no way out. The routed dict provides the exit branch.

## Combining routing with fan-in

A routed edge can have multiple branches eventually flow back into one join:

```python
edges = [
    ('START', classify),
    (classify, {"sql": sql_writer, "chart": chart_maker}),
    ((sql_writer, chart_maker), format_answer),  # fan-in
]
```

Only the branch that actually ran contributes; the join receives the single
selected branch's output.

## When the route isn't deterministic

If the route depends on LLM judgment, have an `LlmAgent` produce a structured
output and a small `FunctionNode` translate it to a route string. Keeps the
routing logic deterministic and unit-testable:

```python
@node
def to_route(node_input: dict):
    return Event(output=node_input, route=node_input["intent"])

edges = [
    ('START', classifier_llm),         # LlmAgent with output_schema
    (classifier_llm, to_route),
    (to_route, {"sql": sql_writer, "chart": chart_maker, "__DEFAULT__": fallback}),
]
```

## Common mistakes

| Mistake | Fix |
|---|---|
| Returning a bare `str` and expecting routing | Return `Event(output=..., route=...)` from the deciding node |
| Cycle with no routed exit | Add a dict edge with at least one route that leaves the cycle |
| Missing `__DEFAULT__` then hitting an unmapped route | Add a default branch or constrain the producer's possible routes |
| Routing on free-form LLM text | Translate via a small function node into a fixed set of route keys |
