import os

import pytest

from nuvel.memory.factory import build_default_service


@pytest.mark.asyncio
async def test_build_default_service_uses_explicit_args(monkeypatch, tmp_path):
    monkeypatch.delenv("NUVEL_ORG_MEMORY_DSN", raising=False)
    monkeypatch.delenv("NUVEL_ORG_GRAPH_PATH", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    graph = tmp_path / "g.yaml"
    graph.write_text(
        "org_id: acme\nlevels: [user, org]\nusers:\n  a:\n    chain:\n"
        "      - {level: user, id: a}\n      - {level: org, id: acme}\n",
        encoding="utf-8",
    )
    svc = await build_default_service(
        dsn="postgresql://placeholder/ignored",
        org_graph_path=str(graph),
        migrate=False,
    )
    assert svc._resolver.org_id == "acme"
    # NullEmbedder when no GOOGLE_API_KEY
    assert await svc._embedder.embed("x") is None


@pytest.mark.asyncio
async def test_build_default_service_missing_env_raises(monkeypatch):
    monkeypatch.delenv("NUVEL_ORG_MEMORY_DSN", raising=False)
    monkeypatch.delenv("NUVEL_ORG_GRAPH_PATH", raising=False)
    with pytest.raises(KeyError):
        await build_default_service()
