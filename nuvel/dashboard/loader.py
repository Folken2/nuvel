"""Trace loader for the dashboard.

Thin wrapper around `nuvel.traces_cli._collect_runs` so the dashboard
and the CLI share one parser and one `Run` schema. Sources are resolved
once at construction so the watcher and HTTP handlers see the same view.

Also side-loads `scored.jsonl` siblings (written by `nuvel eval score`)
so the home view can decorate runs with their overall score and flags
without re-running the scorer.
"""

from __future__ import annotations

from pathlib import Path

from nuvel.eval.schema import ScoredRun
from nuvel.eval.writer import load_scored_index
from nuvel.traces_cli import (
    Run,
    _iter_trace_files,
    _parse_file_runs,
    _sort_runs,
)


class TraceLoader:
    """Loads `Run` records from a fixed list of source directories."""

    def __init__(self, sources: list[Path]) -> None:
        self._sources = sources

    def sources(self) -> list[Path]:
        return list(self._sources)

    def runs(self) -> list[Run]:
        """Return all runs across sources, newest first. Events kept so
        the home view can detect errors via `_run_has_error`."""
        out: list[Run] = []
        for f in _iter_trace_files(self._sources):
            out.extend(_parse_file_runs(f, keep_events=True))
        return _sort_runs(out)

    def scored_index(self) -> dict[str, ScoredRun]:
        """Aggregate every ``scored.jsonl`` across sources into one map.

        Key is ``trace_id``. If multiple agents share a trace_id (rare),
        last-loaded wins — sources are scanned in stable order so it's
        deterministic across requests.
        """
        out: dict[str, ScoredRun] = {}
        for d in self._sources:
            out.update(load_scored_index(d / "scored.jsonl"))
        return out

    def find_by_id(self, id_or_prefix: str) -> Run | None:
        """Find a single run by trace_id or session_id (prefix match)."""
        for f in _iter_trace_files(self._sources):
            for run in _parse_file_runs(f, keep_events=True):
                if (
                    run.trace_id == id_or_prefix
                    or run.session_id == id_or_prefix
                    or (run.trace_id and run.trace_id.startswith(id_or_prefix))
                    or (run.session_id and run.session_id.startswith(id_or_prefix))
                ):
                    return run
        return None
