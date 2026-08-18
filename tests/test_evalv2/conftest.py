"""Shared fixtures for evalv2 tests."""
from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def summarize_skill_dir() -> Path:
    """A skill directory that ships an eval/ suite."""
    return FIXTURES / "summarize-skill"


@pytest.fixture
def no_eval_skill_dir() -> Path:
    """A skill directory with no eval/ directory."""
    return FIXTURES / "no-eval-skill"


@pytest.fixture
def eval_dir(summarize_skill_dir: Path) -> Path:
    """The explicit eval/ directory of the summarize skill."""
    return summarize_skill_dir / "eval"
