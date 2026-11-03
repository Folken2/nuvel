"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_feedback_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trace discovery reads the feedback store's `_runs`; never the developer's real one."""
    monkeypatch.setenv("NUVEL_FEEDBACK_DIR", str(tmp_path_factory.mktemp("feedback") / "none"))


@pytest.fixture(autouse=True)
def _isolate_platform_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never write fixtures to the developer's database: these beat what a `.env` sets."""
    monkeypatch.setenv("NUVEL_PLATFORM_SYNC", "")
    monkeypatch.setenv("NUVEL_PLATFORM_DATABASE_URL", "")
