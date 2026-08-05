"""
Tests for the shared ADK App wiring (plugins + conversation compaction).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.main import adk_app, _build_compaction_config, APP_NAME  # noqa: E402
from outlook_king.plugins.context_budget_plugin import ContextBudgetPlugin  # noqa: E402
from outlook_king.plugins.memory_plugin import MemoryPlugin  # noqa: E402


def test_app_carries_plugins_and_compaction():
    assert adk_app.name == APP_NAME
    plugin_types = {type(p) for p in adk_app.plugins}
    assert MemoryPlugin in plugin_types
    assert ContextBudgetPlugin in plugin_types
    assert adk_app.events_compaction_config is not None
    assert adk_app.events_compaction_config.compaction_interval == 10
    assert adk_app.events_compaction_config.overlap_size == 2


def test_compaction_env_overrides(monkeypatch):
    monkeypatch.setenv("COMPACTION_INTERVAL", "5")
    monkeypatch.setenv("COMPACTION_OVERLAP", "1")
    cfg = _build_compaction_config()
    assert cfg.compaction_interval == 5
    assert cfg.overlap_size == 1


def test_compaction_can_be_disabled(monkeypatch):
    monkeypatch.setenv("COMPACTION_INTERVAL", "0")
    assert _build_compaction_config() is None
