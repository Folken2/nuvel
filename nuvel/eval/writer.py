"""Append-only JSONL writer and indexer for scored runs.

One ``scored.jsonl`` per trace directory — aggregates every scored
``Run`` across all per-day ``*.jsonl`` files in that dir. Idempotency is
enforced upstream in ``scorer.py`` by consulting ``load_scored_index``.
"""
from __future__ import annotations

import logging
from pathlib import Path

from nuvel.eval.schema import ScoredRun


logger = logging.getLogger(__name__)


def append_scored(path: Path, scored: ScoredRun) -> None:
    """Append one ``ScoredRun`` as a single JSON line. Parent dir must exist."""
    line = scored.to_json_line() + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def load_scored_index(path: Path) -> dict[str, ScoredRun]:
    """Return ``{trace_id: latest ScoredRun}`` for ``path``.

    Malformed lines are skipped with a warn-log — never crash the load.
    If a ``trace_id`` appears multiple times (rescoring), the *last*
    occurrence wins, which is what we want.
    """
    out: dict[str, ScoredRun] = {}
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
            scored = ScoredRun.from_json_line(line)
        except Exception as exc:  # noqa: BLE001 — tolerant load
            logger.warning("skipping malformed line %s:%d: %s", path, i, exc)
            continue
        out[scored.trace_id] = scored
    return out
