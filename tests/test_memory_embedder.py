"""Test embedder protocol + implementations."""

import pytest

from nuvel.memory.embedder import Embedder, NullEmbedder


@pytest.mark.asyncio
async def test_null_embedder_returns_none():
    assert await NullEmbedder().embed("anything") is None


@pytest.mark.asyncio
async def test_null_embedder_satisfies_protocol():
    e: Embedder = NullEmbedder()
    assert await e.embed("x") is None
