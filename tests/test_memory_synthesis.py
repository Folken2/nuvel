"""Unit tests for the answer-synthesis + gap-analysis layer.

These exercise the pure pipeline in ``nuvel.memory.synthesis`` directly — no
Postgres, no live LLM. Synthesis prose is driven by an injected mock LLM (or the
zero-LLM fallback); gap analysis is fully deterministic/heuristic so it asserts
without any model. The integration tests drive OrgMemoryService with a canned
store to prove the ``synthesize=`` flag on ``search_memory``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from google.adk.memory.base_memory_service import SearchMemoryResponse

from nuvel.memory import ConfigScopeResolver, MemoryRow, NullEmbedder
from nuvel.memory.org_memory_service import OrgMemoryService
from nuvel.memory.synthesis import (
    GapAnalysis,
    SearchResult,
    analyze_gaps,
    compute_confidence,
    synthesize,
)

FIXTURE = Path(__file__).parent / "fixtures" / "org_graph.yaml"
NOW = datetime(2026, 8, 5, tzinfo=timezone.utc)


def _row(
    content: str,
    *,
    id: str,
    cosine: float = 0.5,
    created_at: datetime | None = None,
) -> MemoryRow:
    return MemoryRow(
        id=id,
        org_id="acme",
        scope_level="user",
        scope_id="u1",
        scope_chain=["user:u1", "org:acme"],
        content=content,
        embedding=None,
        created_at=created_at or NOW,
        score=cosine,
    )


class MockLLM:
    """Injectable synthesis LLM that returns a canned string."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self._response


class BrokenLLM:
    async def complete(self, *, system: str, prompt: str) -> str:
        raise RuntimeError("model exploded")


# ── synthesis ──────────────────────────────────────────────────────────────


async def test_synthesis_produces_answer_text_from_rows():
    rows = [
        _row("Alice leads the Phoenix project.", id="r1"),
        _row("Phoenix ships in Q4.", id="r2"),
    ]
    llm = MockLLM(json.dumps({
        "answer": "Alice leads the Phoenix project [1], which ships in Q4 [2].",
        "citations": [{"index": 1}, {"index": 2}],
        "gaps": [],
    }))
    result = await synthesize("Who leads Phoenix?", rows, llm=llm, now=NOW)
    assert result.used_llm is True
    assert "Alice leads the Phoenix project" in result.answer
    # The rows were handed to the model.
    assert "Alice leads the Phoenix project." in llm.calls[0][1]


async def test_synthesis_includes_citations_mapped_to_rows():
    rows = [
        _row("Alice leads the Phoenix project.", id="r1"),
        _row("Phoenix ships in Q4.", id="r2"),
    ]
    llm = MockLLM(json.dumps({
        "answer": "Alice leads it [1].",
        "citations": [{"index": 1}],
        "gaps": [],
    }))
    result = await synthesize("Who leads Phoenix?", rows, llm=llm, now=NOW)
    assert [c.index for c in result.citations] == [1]
    assert result.citations[0].memory_id == "r1"
    assert result.citations[0].row is rows[0]
    # Only cited rows are surfaced as sources.
    assert [s.id for s in result.sources] == ["r1"]


async def test_synthesis_confidence_high_when_query_fully_covered():
    rows = [_row("Alice works at Acme Corp in Berlin.", id="r1", created_at=NOW)]
    conf = compute_confidence("Where does Alice work?", rows)
    assert conf >= 0.8


async def test_synthesis_confidence_low_when_query_barely_covered():
    rows = [_row("The weather is sunny today.", id="r1")]
    conf = compute_confidence("What is the Zephyr encryption protocol?", rows)
    assert conf <= 0.3


# ── gap analysis ─────────────────────────────────────────────────────────────


def test_gap_stale_entity_notes_date():
    old = NOW - timedelta(days=200)
    rows = [_row("Alice joined the platform team.", id="r1", created_at=old)]
    gaps = analyze_gaps("What is new with Alice?", rows, now=NOW, stale_after_days=30)
    stale = [g for g in gaps.gaps if g.kind == "stale_entity"]
    assert len(stale) == 1
    assert "Alice" in stale[0].message
    assert "since" in stale[0].message.lower()


def test_gap_unknown_topic_when_query_term_has_no_match():
    rows = [_row("Alice works at Acme.", id="r1")]
    gaps = analyze_gaps("What is the Zephyr protocol?", rows, now=NOW)
    unknown = [g for g in gaps.gaps if g.kind == "unknown_topic"]
    assert len(unknown) == 1
    assert "zephyr" in unknown[0].message.lower()


def test_gap_contradiction_detected():
    rows = [
        _row("Alice works at Acme.", id="r1"),
        _row("Alice no longer works at Acme.", id="r2"),
    ]
    gaps = analyze_gaps("Where does Alice work?", rows, now=NOW)
    conflicts = [g for g in gaps.gaps if g.kind == "contradiction"]
    assert len(conflicts) >= 1
    assert "Alice" in conflicts[0].message


def test_gap_none_when_query_fully_covered_and_fresh():
    rows = [_row("Alice works at Acme Corp.", id="r1", created_at=NOW)]
    gaps = analyze_gaps("Where does Alice work?", rows, now=NOW)
    assert gaps.gaps == []
    assert bool(gaps) is False


# ── fallback ─────────────────────────────────────────────────────────────────


async def test_fallback_ranked_list_when_no_llm():
    rows = [
        _row("Alice leads Phoenix.", id="r1", cosine=0.9),
        _row("Bob is on the team.", id="r2", cosine=0.7),
    ]
    result = await synthesize("Who leads Phoenix?", rows, llm=None, now=NOW)
    assert result.used_llm is False
    assert "Alice leads Phoenix." in result.answer
    assert "Bob is on the team." in result.answer
    # every row is a citation in fallback mode
    assert {c.memory_id for c in result.citations} == {"r1", "r2"}


async def test_fallback_when_llm_raises():
    rows = [_row("Alice leads Phoenix.", id="r1")]
    result = await synthesize("Who leads Phoenix?", rows, llm=BrokenLLM(), now=NOW)
    assert result.used_llm is False
    assert "Alice leads Phoenix." in result.answer


async def test_synthesize_empty_rows_returns_gapful_result():
    result = await synthesize("Who leads Phoenix?", [], llm=None, now=NOW)
    assert result.rows == []
    assert result.confidence == 0.0
    assert bool(result.gaps) is True


# ── integration with OrgMemoryService ────────────────────────────────────────


@dataclass
class StubSearchStore:
    canned: list[MemoryRow] = field(default_factory=list)
    last_search: dict = field(default_factory=dict)

    async def insert(self, row: MemoryRow) -> str:  # pragma: no cover - unused
        return "x"

    async def search(self, **kwargs) -> list[MemoryRow]:
        self.last_search = kwargs
        return list(self.canned)

    async def move(self, *_: object, **__: object) -> None: ...
    async def delete(self, *_: object) -> None: ...
    async def list_by_scope(self, *_: object, **__: object) -> list[MemoryRow]:
        return []


def _svc(canned: list[MemoryRow]) -> OrgMemoryService:
    return OrgMemoryService(
        store=StubSearchStore(canned=canned),
        resolver=ConfigScopeResolver.from_yaml(FIXTURE),
        embedder=NullEmbedder(),
    )


async def test_search_memory_synthesize_true_returns_search_result():
    rows = [_row("Alice works at Acme Corp.", id="r1", created_at=NOW)]
    svc = _svc(rows)
    result = await svc.search_memory(
        app_name="agent", user_id="albert", query="Where does Alice work?", synthesize=True
    )
    assert isinstance(result, SearchResult)
    assert result.rows[0].content == "Alice works at Acme Corp."
    assert isinstance(result.gaps, GapAnalysis)
    assert isinstance(result.answer, str) and result.answer


async def test_search_memory_synthesize_false_is_backward_compatible():
    rows = [_row("Alice works at Acme Corp.", id="r1")]
    svc = _svc(rows)
    resp = await svc.search_memory(app_name="agent", user_id="albert", query="policy")
    assert isinstance(resp, SearchMemoryResponse)
    assert len(resp.memories) == 1
