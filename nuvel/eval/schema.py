"""Wire format for scored runs.

`ScoredRun` is the canonical object written one-per-line to
`scored.jsonl` siblings of trace files. Bumping `SCORER_VERSION` is a
deliberate signal that previously-written rows are stale relative to the
current scoring logic and should be rescored.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


SCORER_VERSION = "1.0"


class Flag:
    """Heuristic flag names. Plain string constants — easy to JSON-serialize.

    Not a ``StrEnum`` because we want forward-compat: a future scorer can
    write a flag this version doesn't know about and we still deserialize.
    """

    TOOL_ERROR = "tool_error"
    NO_ASSISTANT_OUTPUT = "no_assistant_output"
    EXCESSIVE_TURNS = "excessive_turns"
    COST_OUTLIER = "cost_outlier"
    LATENCY_OUTLIER = "latency_outlier"
    TOOL_LOOP = "tool_loop"
    TOKEN_BLOAT = "token_bloat"
    INCOMPLETE_TRACE = "incomplete_trace"


@dataclass
class JudgeResult:
    """Output of one LLM-judge call. ``error`` is set iff the call failed."""

    model: str
    success: float | None = None
    quality: float | None = None
    notes: str = ""
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ScoredRun:
    """One scored row. Goes to scored.jsonl as a single JSON line."""

    trace_id: str
    agent: str
    scored_at: str  # ISO 8601 UTC
    scorer_version: str
    rubric_version: str
    overall: float
    components: dict[str, float]  # success, quality, efficiency, reliability
    flags: list[str] = field(default_factory=list)
    judge: dict[str, Any] = field(default_factory=dict)
    skipped_judge: bool = False

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json_line(cls, line: str) -> "ScoredRun":
        data = json.loads(line)
        return cls(**data)
