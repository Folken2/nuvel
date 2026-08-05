from __future__ import annotations

import pytest

from google.adk.cli.service_registry import get_service_registry

from nuvel.memory.adk_registry import (
    ORG_MEMORY_SCHEME,
    register_org_memory_scheme,
)


def test_registration_idempotent():
    register_org_memory_scheme()
    register_org_memory_scheme()  # second call must not raise
    registry = get_service_registry()
    assert ORG_MEMORY_SCHEME in registry._memory_factories


def test_scheme_constant_matches_doc():
    # Operator-visible string — guard against accidental renames.
    assert ORG_MEMORY_SCHEME == "nuvel-org-memory"


def test_factory_raises_when_dsn_missing(monkeypatch):
    monkeypatch.delenv("NUVEL_ORG_MEMORY_DSN", raising=False)
    monkeypatch.delenv("NUVEL_ORG_GRAPH_PATH", raising=False)
    register_org_memory_scheme()
    factory = get_service_registry()._memory_factories[ORG_MEMORY_SCHEME]
    with pytest.raises(RuntimeError, match="NUVEL_ORG_MEMORY_DSN"):
        factory("nuvel-org-memory://default")


def test_factory_returns_org_memory_service(monkeypatch, tmp_path):
    from nuvel.memory.org_memory_service import OrgMemoryService

    monkeypatch.setenv("NUVEL_ORG_MEMORY_DSN", "postgresql://placeholder/ignored")
    graph = tmp_path / "g.yaml"
    graph.write_text(
        "org_id: acme\nlevels: [user, org]\nusers:\n  a:\n    chain:\n"
        "      - {level: user, id: a}\n      - {level: org, id: acme}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NUVEL_ORG_GRAPH_PATH", str(graph))
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    register_org_memory_scheme()
    factory = get_service_registry()._memory_factories[ORG_MEMORY_SCHEME]
    # `migrate=0` query param avoids hitting a real DB.
    svc = factory("nuvel-org-memory://default?migrate=0")
    assert isinstance(svc, OrgMemoryService)
