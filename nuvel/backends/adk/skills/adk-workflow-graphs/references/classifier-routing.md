# Classifier routing — `ClassifierNode`

Requires `google-adk>=2.13.0`. Lives in `google.adk.labs.classifiers`, an
**experimental** package: the interface may change between releases, and it
logs a warning that the `CLASSIFIER` feature is enabled when used.

Many graphs branch on a judgment about the input — which team owns a ticket,
whether a request is urgent. The 2.0 way is an `LlmAgent` that returns a label
plus a function node that maps the label to `Event(route=...)`. A
`ClassifierNode` replaces both: it asks a classifier one typed question and
emits the answer as the route.

What it gives you over the hand-rolled version:

- **The route is always a label you declared.** The classifier can only
  answer with the question's labels, so there is no "model said `Billing.`"
  string-matching bug.
- **Pass-through output.** The node's output is its unchanged input, so the
  routed-to node receives what the classifier saw, not the answer.
- **Build-time graph checks.** A misspelled route, or a label with no edge,
  raises a pydantic `ValidationError` when the `Workflow` is constructed
  instead of silently ending a branch at run time.

## Choice → one route per label

```python
from google.adk import Agent
from google.adk.labs.classifiers import Choice, ClassifierNode, LlmClassifier
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import Workflow

# Any BaseLlm works; generated agents route through OpenRouter via LiteLlm.
classifier = LlmClassifier(model=LiteLlm(model="openrouter/google/gemini-2.5-flash"))

triage = ClassifierNode(
    name="triage",
    classifier=classifier,
    question=Choice(
        instructions="Which team should handle this support ticket?",
        criteria={
            "billing": "Invoices, refunds, or subscription charges",
            "technical": "Bugs, errors, outages, or API problems",
        },
    ),
)

root_agent = Workflow(
    name="support_router",
    edges=[
        ("START", triage),
        (triage, {"billing": billing_agent, "technical": technical_agent}),
    ],
)
```

The keys of `criteria` are the labels **and** the routes — the edge dict must
use the same strings. A `Choice` needs at least two labels.

Catch-all: map the labels you care about and send the rest to
`DEFAULT_ROUTE` (`"__DEFAULT__"`):

```python
from google.adk.workflow import DEFAULT_ROUTE

(triage, {"billing": billing_agent, DEFAULT_ROUTE: general_queue})
```

## Binary → `True` / `False` routes

```python
from google.adk.labs.classifiers import Binary

is_urgent = ClassifierNode(
    name="is_urgent",
    classifier=classifier,
    question=Binary(
        instructions="Does this ticket describe an outage?",
        criteria="The customer cannot use the product in production.",
    ),
    threshold=0.7,               # P(true) at or above this routes to True
    answer_key="urgency_answer", # optional: full answer dict into ctx.state
)

edges = [
    ("START", triage),
    (triage, {"billing": billing_agent, "technical": is_urgent}),
    (is_urgent, {True: page_oncall, False: eng_queue}),
]
```

Routes on a `Binary` are the bools `True`/`False`, not the strings
`"true"`/`"false"` — the string form fails validation. `threshold` is only
valid on a `Binary`; setting it on a `Choice` is a `ValidationError`.

With `answer_key` set, later nodes read the probabilities:
`ctx.state["urgency_answer"]["probability"]` for a `Binary`, or the
per-label `probabilities` dict for a `Choice`.

## Failure behavior

The node raises `WorkflowDataError` — it never guesses a route — when it gets
no input, or the classifier refuses, returns no answer, answers outside the
labels, or answers with the wrong type. Wrap it with the node's
`retry_config` if the classifier is flaky, and keep a `DEFAULT_ROUTE` edge
only for labels you deliberately don't handle, not as an error catch.

## Choosing a classifier

| Classifier | When |
|------------|------|
| `LlmClassifier(model=...)` | Default. Any `BaseLlm` (string name or `LiteLlm(...)`), constrained to the labels via structured output. Probabilities are the model's *stated* values — check calibration with evals before trusting a `threshold`. |
| `TypesafeClassifier` (`google.adk.integrations.typesafe`) | High-volume routing with TypeSafe's Jev models: calibrated probabilities in one forward pass, much faster and cheaper than an LLM. Needs a TypeSafe account. |
| Your own `BaseClassifier` subclass | An existing in-house model, or a deterministic rule set you want behind the same node. |

Swapping classifiers never changes the node or the edges.

## Limits

- **One question per node** — a node emits one route. Two independent
  decisions take two nodes (or one `classifier.classify(questions={...})`
  call inside your own function node, which is cheaper when both depend only
  on the original input).
- **No `Score` questions** — a number on a scale isn't a route. Use a
  `Choice` whose labels name the ranges (`"low"`, `"high"`).
- **Input must suit the classifier** — a text-only classifier raises on
  `types.Content` with image parts, failing the node.
