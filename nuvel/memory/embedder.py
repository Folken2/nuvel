"""Embedder protocol + default Google impl + NullEmbedder for tests."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Protocol

log = logging.getLogger(__name__)


class Embedder(Protocol):
    dim: int

    async def embed(self, text: str) -> list[float] | None: ...


class NullEmbedder:
    dim = 1536

    async def embed(self, text: str) -> list[float] | None:  # noqa: ARG002
        return None


class GoogleEmbedder:
    """Lazy wrapper around google-genai's text-embedding-004 (768-dim).

    Falls back to NullEmbedder behavior (returns None) on any error so the
    OrgMemoryService write path stays available — the row is inserted with
    embedding=NULL and read via pg_trgm lexical fallback.
    """

    dim = 768

    def __init__(self, model: str = "text-embedding-004", api_key: str | None = None) -> None:
        self.model = model
        self._api_key = api_key or os.getenv("GOOGLE_API_KEY")
        self._client = None

    def _ensure(self) -> None:
        if self._client is not None:
            return
        from google import genai  # type: ignore

        self._client = genai.Client(api_key=self._api_key) if self._api_key else genai.Client()

    def _sync_embed(self, text: str) -> list[float] | None:
        try:
            self._ensure()
            assert self._client is not None
            resp = self._client.models.embed_content(model=self.model, contents=text)
            return list(resp.embeddings[0].values)
        except Exception as exc:  # noqa: BLE001
            log.warning("embed failed (%s); returning None for lexical fallback", exc)
            return None

    async def embed(self, text: str) -> list[float] | None:
        return await asyncio.get_running_loop().run_in_executor(None, self._sync_embed, text)
