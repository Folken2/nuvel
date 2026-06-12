"""Wire formats for replay A/B + their append-only JSONL persistence.

``Variant`` is the declarative config-delta authored as YAML. ``ReplayResult``
is one replayed-and-scored trace, written one-per-line to
``traces/replays/<variant-name>.jsonl``. ``REPLAY_VERSION`` versions the replay
*machinery* (message assembly / synthetic-run shape); the variant's own
``version`` field is the idempotency key for rescoring.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nuvel._defaults import DEFAULT_FAST_MODEL

logger = logging.getLogger(__name__)

# Bump only when replay assembly/synthetic-run logic changes — NOT for variant tweaks.
REPLAY_VERSION = "1.0"


@dataclass
class Variant:
    """A config delta to A/B against the baseline. Loaded from YAML."""

    version: str          # idempotency key; bumping forces a rescore
    name: str
    system_prompt: str
    description: str = ""
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 600

    def resolved_model(self) -> str:
        """Priority chain: variant.model → EVAL_JUDGE_MODEL → DEFAULT_FAST_MODEL.

        Mirrors ``Rubric.resolved_model`` so model selection is consistent
        across scoring and replay.
        """
        if self.model:
            return self.model
        env_model = os.getenv("EVAL_JUDGE_MODEL")
        if env_model:
            return env_model
        return DEFAULT_FAST_MODEL


@dataclass
class ReplayResult:
    """One replayed trace: the variant's output plus its ScoredRun blob."""

    trace_id: str
    agent: str
    variant_name: str
    variant_version: str
    replayed_at: str          # ISO 8601 UTC
    model: str
    output_text: str
    replay_cost_usd: float
    scored: dict[str, Any] = field(default_factory=dict)  # asdict(ScoredRun)
    replay_version: str = REPLAY_VERSION

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json_line(cls, line: str) -> "ReplayResult":
        return cls(**json.loads(line))


def append_replay(path: Path, result: ReplayResult) -> None:
    """Append one ``ReplayResult`` as a single JSON line. Parent dir must exist."""
    with path.open("a", encoding="utf-8") as f:
        f.write(result.to_json_line() + "\n")


def load_replay_index(path: Path) -> dict[str, ReplayResult]:
    """Return ``{trace_id: latest ReplayResult}``. Last occurrence wins.

    Mirrors ``nuvel.eval.writer.load_scored_index`` — tolerant of malformed
    lines (warn + skip), returns empty dict for a missing file.
    """
    out: dict[str, ReplayResult] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return out
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            result = ReplayResult.from_json_line(line)
        except Exception as exc:  # noqa: BLE001 — tolerant load
            logger.warning("skipping malformed line %s:%d: %s", path, i, exc)
            continue
        out[result.trace_id] = result
    return out
