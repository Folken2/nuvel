"""evalv2 judges — the evaluators the runner applies to a skill's output.

Three families, each independently testable:

- ``deterministic`` — pure rule checks (exact/fuzzy match, length, keywords,
  json-schema). No model.
- ``llm`` — a weighted-rubric LLM judge behind a ``judge_fn`` seam.
- ``consistency`` — self-consistency across repeated executor runs.

All model access is lazy and injectable so importing this package (and the
test path) never requires litellm or the network.
"""
from __future__ import annotations

from .consistency import Executor, run_consistency
from .deterministic import run_deterministic_checks
from .llm import Rubric, judge_output

__all__ = [
    "run_deterministic_checks",
    "Rubric",
    "judge_output",
    "run_consistency",
    "Executor",
]
