"""Test embedder protocol + implementations."""

import pytest

from nuvel.memory.embedder import Embedder, NullEmbedder


def test_null_embedder_returns_none():
    assert NullEmbedder().embed("anything") is None


def test_null_embedder_satisfies_protocol():
    e: Embedder = NullEmbedder()
    assert e.embed("x") is None
