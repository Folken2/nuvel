# Build a workflow-native agent

`--workflow` makes the root agent an ADK 2.0 `Workflow` graph instead of a single `LlmAgent`. You get a plan → route → execute skeleton with typed contracts between the nodes and a deterministic branch.

ADK only.

## When to use it

Use `--workflow` when the work is **genuinely multi-step with branching**:

- plan-then-execute — decide what to do, then do it
- triage-then-route — classify the request, send it down different paths
- draft-then-review — produce something, then critique or approve it

Skip it for a single-purpose tool-using agent. One `LlmAgent` with three tools does not need a graph; a graph you don't need is just indirection you have to maintain.

## Scaffold

```bash
nuvel new triage-bot --workflow --description "triage GitHub issues, route to the right team"
```

You'll see `workflow` in the flags line of the output.

## What lands

| File | Role |
| --- | --- |
| `triage_bot/agent_workflow.py` | The real root agent — nodes, typed contracts, routing, the `Workflow` graph. |
| `triage_bot/agent.py` | A shim that re-exports `root_agent` from `agent_workflow.py`. |

Everything else is the standard ADK skeleton — same `run_adk.py`, same plugin chain, same `tools/`, `prompt/`, `skills/`.

The shim exists so `triage_bot.agent:root_agent` stays the one import path. `run_adk.py`, `adk web`, the gateway overlays, and the cron runner never need to know which shape the root agent has.

## The three nodes

| Node | Mode | Model | Output schema | Tools |
| --- | --- | --- | --- | --- |
| `planner` | `task` | `REASONING_MODEL` | `Plan` | — |
| `executor` | `task` | `FAST_MODEL` | `Outcome` | `get_tools()` |
| `decline` | `single_turn` | `FAST_MODEL` | `Outcome` | — |

- **`planner`** restates the request as a concrete goal and breaks it into ordered steps. `mode='task'` means it may come back to the user for clarification and finishes by calling the auto-attached `finish_task` tool. If the request can't be met with the available tools it sets `feasible=false` and explains why.
- **`executor`** receives the plan, works through the steps with the project's tools, and records each one under `completed` or `skipped`. Also `mode='task'` — it can ask before doing anything destructive.
- **`decline`** handles infeasible plans. `mode='single_turn'`: one bounded response, no tools, no follow-up turns.

## Typed contracts

Every node that hands data onward declares an `output_schema`:

```python
class Plan(BaseModel):
    goal: str = Field(description="The user's request, restated concretely.")
    steps: list[str] = Field(description="Ordered steps that satisfy the goal.")
    feasible: bool = Field(
        description="False when the request cannot be met with the available tools."
    )
    reason: str = Field(default="", description="Why the request is infeasible.")
```

For a `mode='task'` agent the schema also becomes the **parameter list of the auto-attached `finish_task` tool**. The agent literally cannot finish without producing schema-valid output — there is no free-form "I think I'm done" path. A node's validated output becomes the next node's `node_input`.

## Routing

Routes are decided by a plain function node reading the planner's typed output — never by routing on free-form model text:

```python
@node
def route_plan(node_input: Plan) -> Event:
    route: Literal["execute", "decline"] = (
        "execute" if node_input.feasible and node_input.steps else "decline"
    )
    return Event(output=node_input, route=route)
```

And the graph:

```python
root_agent = Workflow(
    name="triage_bot",
    description="...",
    edges=[
        ("START", planner),
        (planner, route_plan),
        (route_plan, {"execute": executor, "__DEFAULT__": decline}),
    ],
)
```

!!! note "Why `__DEFAULT__` and not `"decline"`"
    Graph validation rejects two edges between the same pair of nodes, so a named route key and `__DEFAULT__` can't share a target. The template makes `decline` the default branch rather than giving it its own key.

## Customize

**Add a node.** Define an `LlmAgent` with a `mode` and an `output_schema`, then wire an edge to it:

```python
reviewer = LlmAgent(
    model=REASONING_MODEL,
    name="reviewer",
    description="Checks the outcome against the plan before it reaches the user.",
    instruction="You receive an Outcome. Verify every planned step is accounted for...",
    mode="task",
    output_schema=Outcome,
)

# edges=[..., (executor, reviewer)]
```

**Change the routing logic.** Add fields to `Plan` and branch on them. A triage bot might route by team:

```python
@node
def route_plan(node_input: Plan) -> Event:
    if not node_input.feasible:
        return Event(output=node_input, route="decline")
    return Event(output=node_input, route=node_input.team)  # "backend" | "frontend" | ...
```

```python
(route_plan, {"backend": backend_agent, "frontend": frontend_agent, "__DEFAULT__": decline}),
```

**Give a node different tools.** `tools=` is per node. Only `executor` gets `get_tools()` in the template; scope each node to what it actually needs.

## Read the skills

Two bundled skills carry the full API — load them into your coding agent before making structural changes:

- **`adk-workflow-graphs`** — nodes and edges, conditional routing, fan-out/fan-in, dynamic nodes, human-in-the-loop revision cycles.
- **`adk-task-delegation`** — `mode='task'` / `'single_turn'` / `'chat'`, the `finish_task` tool, `input_schema` / `output_schema`.

```bash
nuvel skills search workflow
```

## Run it

Nothing special — a `Workflow` is a `BaseNode`, so it drops into the harness exactly like an `LlmAgent`:

```bash
cd generated-agents/triage-bot
pip install -r requirements.txt
DEV_MODE=true python run_adk.py
```

## Going back to a single agent

Replace `root_agent` in `agent_workflow.py` with a plain `LlmAgent` — or delete the file and write a normal `agent.py`. Nothing else in the project assumes a graph.
