"""Rubric loading: default ships in code; per-agent override via YAML.

A rubric configures (a) component weights used to compute ``overall``,
(b) which judge model to call, and (c) optional extra criteria appended
to the judge prompt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


_DEFAULT_WEIGHTS: dict[str, float] = {
    "success": 0.4,
    "quality": 0.3,
    "efficiency": 0.15,
    "reliability": 0.15,
}

DEFAULT_RUBRIC_VERSION = "default-1.0"

# Imported lazily so tests that don't touch the judge needn't pull defaults.
from nuvel._defaults import DEFAULT_FAST_MODEL


@dataclass
class Rubric:
    """Scoring rubric. Frozen after load; tests construct directly."""

    version: str = DEFAULT_RUBRIC_VERSION
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))
    judge_model: str | None = None  # rubric-level override
    extra_criteria: str = ""

    def resolved_model(self) -> str:
        """Apply the documented priority chain: rubric → env → DEFAULT_FAST_MODEL."""
        if self.judge_model:
            return self.judge_model
        env_model = os.getenv("EVAL_JUDGE_MODEL")
        if env_model:
            return env_model
        return DEFAULT_FAST_MODEL


DEFAULT_RUBRIC = Rubric()


def _rubric_path_for(agent: str) -> Path:
    """generated-agents/<agent>/evals/rubric.yaml — by convention."""
    return Path("generated-agents") / agent / "evals" / "rubric.yaml"


def load_rubric(agent: str) -> Rubric:
    """Return per-agent rubric if present, else the default.

    Raises ``ValueError`` if the YAML exists but is malformed — fail-fast
    so a misconfigured rubric doesn't silently revert to defaults.
    """
    # Agent may have a sub-agent suffix like "outlook-king/composer";
    # rubrics live at the top-level agent dir.
    top_level = agent.split("/")[0]
    path = _rubric_path_for(top_level)
    if not path.is_file():
        return DEFAULT_RUBRIC

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: rubric root must be a mapping, got {type(data).__name__}")

    weights = data.get("weights") or _DEFAULT_WEIGHTS
    if not isinstance(weights, dict):
        raise ValueError(f"{path}: weights must be a mapping")

    judge = data.get("judge") or {}
    if not isinstance(judge, dict):
        raise ValueError(f"{path}: judge must be a mapping")

    extra = data.get("extra_criteria") or judge.get("extra_criteria") or ""
    return Rubric(
        version=str(data.get("version") or DEFAULT_RUBRIC_VERSION),
        weights={k: float(v) for k, v in weights.items()},
        judge_model=judge.get("model"),
        extra_criteria=str(extra),
    )
