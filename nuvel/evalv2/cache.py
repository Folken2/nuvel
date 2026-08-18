"""Deterministic per-sample cache for scored examples.

The cache key is a stable sha256 of ``{skill}|{model}|{input_hash}`` so the
same example scored with the same skill+model always maps to the same file.
One JSON file per key lives under ``<cache_dir>/<skill>/<key>.json``.

Writes are atomic (temp file + rename). Reads are tolerant: a corrupt or
unparseable file is deleted and treated as a miss rather than raising, so a
partial write from a crashed run can never poison future lookups.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .exceptions import CacheError
from .schema import ScoredExample


_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "nuvel" / "evalv2"


def _safe_component(value: str) -> str:
    """Make a string safe to use as a single path component."""
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in value) or "_"


class SampleCache:
    """A filesystem-backed cache of `ScoredExample` results."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR

    def key(self, skill: str, model: str, input_hash: str) -> str:
        """Return the deterministic sha256 key for a (skill, model, input)."""
        raw = f"{skill}|{model}|{input_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, skill: str, model: str, input_hash: str) -> Path:
        key = self.key(skill, model, input_hash)
        return self.cache_dir / _safe_component(skill) / f"{key}.json"

    def get(self, skill: str, model: str, input_hash: str) -> ScoredExample | None:
        """Return the cached example, or ``None`` on miss / corrupt file."""
        path = self._path(skill, model, input_hash)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            example = ScoredExample.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            # Tolerant: drop the poisoned entry and report a miss.
            try:
                path.unlink()
            except OSError:
                pass
            return None
        example.cache_hit = True
        return example

    def put(self, skill: str, model: str, input_hash: str, example: ScoredExample) -> None:
        """Atomically store a scored example under its key."""
        path = self._path(skill, model, input_hash)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(example.to_dict(), indent=2)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            raise CacheError(f"failed to write cache entry for '{skill}': {exc}") from exc

    def clear(self) -> None:
        """Remove the entire cache directory tree."""
        if self.cache_dir.exists():
            try:
                shutil.rmtree(self.cache_dir)
            except OSError as exc:
                raise CacheError(f"failed to clear cache: {exc}") from exc
