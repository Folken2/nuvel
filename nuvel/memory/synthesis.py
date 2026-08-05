"""Answer-synthesis + gap-analysis layer on top of hybrid search.

This is the "GBrain gives you the answer, not raw pages" layer, reimplemented
for Nuvel's org-scoped memory model. It is a *thin* pass over the ranked
``MemoryRow`` results that :mod:`nuvel.memory.hybrid` already produces — it never
re-ranks or replaces search.

Two responsibilities, deliberately split by determinism:

* **Synthesis** (:func:`synthesize`) turns the top-N rows into a cited prose
  answer. Prose is the one place an LLM earns its keep (gbrain's ``think`` does
  the same), so the model is dependency-injected via the :class:`SynthesisLLM`
  protocol and stubbed in tests. When no LLM is available — or the call fails or
  returns garbage — it degrades gracefully to a ranked-list answer.

* **Gap analysis** (:func:`analyze_gaps`) is the "what the brain doesn't know
  yet" note. It is fully deterministic/heuristic: stale entities (nothing added
  since a date), unknown topics (query terms nothing matched) and contradictory
  facts (a negated claim sitting next to its affirmative twin). No LLM, so it is
  cheap, reproducible and unit-testable without a model.

Everything here is pure and side-effect-free apart from the awaited LLM call, so
it unit-tests by feeding mock ``MemoryRow`` objects straight in — no database.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from nuvel.memory.extraction import extract_entity_names
from nuvel.memory.store import MemoryRow

# Rows past this rank aren't worth the synthesis token budget; gbrain caps the
# gather→synthesize hand-off similarly.
DEFAULT_TOP_N = 8

# An entity whose freshest memory is older than this is flagged stale.
DEFAULT_STALE_AFTER_DAYS = 30

# Query words that carry no retrieval signal — dropped before coverage/gap math.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an and or the of to in on at for with about as by from into over under
    what who whom whose which where when why how does do did is are was were be
    been being has have had can could should would will shall may might must
    we you they he she it i me my our your their this that these those there
    know tell show find give any some all more most new old currently now recently
    """.split()
)

# Negation markers that flip a claim's polarity, for contradiction detection.
_NEGATION_RE = re.compile(
    r"\b(?:not|never|neither|none|nobody|cannot|no\s+longer|no\s+more)\b|n't",
    re.IGNORECASE,
)

_WORD_RE = re.compile(r"[a-z0-9]+")


class SynthesisLLM(Protocol):
    """Prose-synthesis LLM seam. Any object with an async ``complete`` works;
    tests inject a stub, production wires a gateway/LiteLLM client. Kept minimal
    on purpose — synthesis needs one system+user turn, nothing else."""

    async def complete(self, *, system: str, prompt: str) -> str: ...


@dataclass
class Citation:
    """A claim's support: 1-based marker index → the backing memory row."""

    index: int
    memory_id: str | None
    row: MemoryRow


@dataclass
class Gap:
    """One thing the brain doesn't (fully) know about the query.

    ``kind`` is one of ``stale_entity`` | ``unknown_topic`` | ``contradiction``.
    ``entity`` is set when the gap is anchored to a named entity.
    """

    kind: str
    message: str
    entity: str | None = None


@dataclass
class GapAnalysis:
    """The collected gaps. Truthy iff at least one gap was found."""

    gaps: list[Gap] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.gaps)

    @property
    def messages(self) -> list[str]:
        return [g.message for g in self.gaps]


@dataclass
class SearchResult:
    """A synthesized answer: prose + citations + confidence + gaps, plus the
    raw ranked ``rows`` for programmatic callers who want the underlying hits."""

    query: str
    answer: str
    sources: list[MemoryRow]
    citations: list[Citation]
    confidence: float
    gaps: GapAnalysis
    rows: list[MemoryRow]
    used_llm: bool


# ── term helpers ─────────────────────────────────────────────────────────────


def _significant_terms(text: str) -> list[str]:
    """Lowercase content-bearing tokens (len ≥ 3, non-stopword), order-preserving
    and de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in _WORD_RE.findall(text.lower()):
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _combined_content(rows: list[MemoryRow]) -> str:
    return "\n".join(r.content for r in rows).lower()


def _term_matched(term: str, haystack: str) -> bool:
    # Substring match so a query stem ("work") still hits an inflected form
    # ("works") in the retrieved content.
    return term in haystack


# ── confidence ───────────────────────────────────────────────────────────────


def compute_confidence(query: str, rows: list[MemoryRow]) -> float:
    """How well the retrieved rows cover the query, in ``[0, 1]``.

    Coverage = fraction of significant query terms that appear anywhere in the
    result set. A query with no significant terms scores 1.0 when anything was
    retrieved, else 0.0. Deterministic — no model, no scores consulted.
    """
    if not rows:
        return 0.0
    terms = _significant_terms(query)
    if not terms:
        return 1.0
    haystack = _combined_content(rows)
    matched = sum(1 for t in terms if _term_matched(t, haystack))
    return round(matched / len(terms), 3)


# ── gap analysis ─────────────────────────────────────────────────────────────


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _stale_entity_gaps(
    entities: set[str], rows: list[MemoryRow], now: datetime, stale_after_days: int
) -> list[Gap]:
    gaps: list[Gap] = []
    for entity in sorted(entities):
        needle = entity.lower()
        dated = [
            r for r in rows if r.created_at is not None and needle in r.content.lower()
        ]
        if not dated:
            continue
        latest = max(_as_utc(r.created_at) for r in dated)  # type: ignore[arg-type]
        age_days = (_as_utc(now) - latest).days
        if age_days > stale_after_days:
            gaps.append(
                Gap(
                    kind="stale_entity",
                    entity=entity,
                    message=(
                        f"Nothing has been added about {entity} since "
                        f"{latest.date().isoformat()}."
                    ),
                )
            )
    return gaps


def _unknown_topic_gap(query: str, rows: list[MemoryRow]) -> Gap | None:
    terms = _significant_terms(query)
    if not terms:
        return None
    haystack = _combined_content(rows)
    missing = [t for t in terms if not _term_matched(t, haystack)]
    if not missing:
        return None
    return Gap(
        kind="unknown_topic",
        message=(
            "No memories mention: " + ", ".join(missing) + "."
        ),
    )


def _contradiction_gaps(entities: set[str], rows: list[MemoryRow]) -> list[Gap]:
    """Flag an entity whose rows carry a negated claim beside an affirmative one.

    Heuristic, not semantic: for each entity mentioned by ≥2 rows, a pair where
    exactly one row is negated and both share a non-entity content token is
    surfaced as a conflict. Subtle/semantic contradictions are out of scope for
    the deterministic pass (an LLM layer could extend this).
    """
    gaps: list[Gap] = []
    for entity in sorted(entities):
        needle = entity.lower()
        mentioning = [r for r in rows if needle in r.content.lower()]
        if len(mentioning) < 2:
            continue
        entity_tokens = set(_WORD_RE.findall(needle))
        found = False
        for i in range(len(mentioning)):
            for j in range(i + 1, len(mentioning)):
                a, b = mentioning[i], mentioning[j]
                neg_a = bool(_NEGATION_RE.search(a.content))
                neg_b = bool(_NEGATION_RE.search(b.content))
                if neg_a == neg_b:
                    continue  # both or neither negated → not a polarity conflict
                shared = (
                    set(_significant_terms(a.content))
                    & set(_significant_terms(b.content))
                ) - entity_tokens
                if not shared:
                    continue
                gaps.append(
                    Gap(
                        kind="contradiction",
                        entity=entity,
                        message=(
                            f"Conflicting information about {entity}: "
                            f"{a.content.strip()!r} vs {b.content.strip()!r}."
                        ),
                    )
                )
                found = True
                break
            if found:
                break
    return gaps


def analyze_gaps(
    query: str,
    rows: list[MemoryRow],
    *,
    now: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> GapAnalysis:
    """Deterministic "what the brain doesn't know" analysis for a result set.

    Detects, with no LLM: stale entities (freshest memory older than
    ``stale_after_days``), unknown topics (significant query terms nothing
    matched) and contradictions (a negated claim beside its affirmative twin).
    """
    now = now or datetime.now(timezone.utc)
    entities = extract_entity_names(query)

    gaps: list[Gap] = []
    gaps.extend(_stale_entity_gaps(entities, rows, now, stale_after_days))
    unknown = _unknown_topic_gap(query, rows)
    if unknown is not None:
        gaps.append(unknown)
    gaps.extend(_contradiction_gaps(entities, rows))
    return GapAnalysis(gaps=gaps)


# ── synthesis ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a memory synthesis engine. Answer the question using ONLY the numbered
memories provided. Each memory is tagged [N]. Cite every claim inline with its
[N] marker(s) immediately after the claim. Never invent facts or markers; if the
memories don't answer the question, say so plainly. Do not instruct the user.

Respond with a single JSON object, no prose outside it:
{
  "answer": "<markdown prose with inline [N] citations>",
  "citations": [{"index": 1}, {"index": 2}]
}
The "index" values must be the [N] numbers you actually cited."""


def _render_rows(rows: list[MemoryRow]) -> str:
    lines: list[str] = []
    for i, row in enumerate(rows, start=1):
        when = ""
        if row.created_at is not None:
            when = f" (recorded {_as_utc(row.created_at).date().isoformat()})"
        lines.append(f"[{i}]{when} {row.content.strip()}")
    return "\n".join(lines)


def _parse_llm_json(text: str) -> dict:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
    stripped = re.sub(r"\s*```$", "", stripped)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", stripped)
        if not m:
            raise
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict):
        raise ValueError("synthesis LLM did not return a JSON object")
    return obj


def _citations_from_indices(
    indices: list[int], rows: list[MemoryRow]
) -> list[Citation]:
    out: list[Citation] = []
    seen: set[int] = set()
    for idx in indices:
        if not isinstance(idx, int) or idx < 1 or idx > len(rows) or idx in seen:
            continue
        seen.add(idx)
        row = rows[idx - 1]
        out.append(Citation(index=idx, memory_id=row.id, row=row))
    return out


def _structured_citation_indices(raw: object) -> list[int]:
    indices: list[int] = []
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, dict) and isinstance(c.get("index"), int):
                indices.append(c["index"])
            elif isinstance(c, int):
                indices.append(c)
    return indices


def _inline_citation_indices(answer: str) -> list[int]:
    return [int(m) for m in re.findall(r"\[(\d+)\]", answer)]


def _llm_synthesize(
    query: str, rows: list[MemoryRow], text: str
) -> tuple[str, list[Citation]]:
    obj = _parse_llm_json(text)
    answer = obj.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("synthesis LLM returned an empty answer")
    indices = _structured_citation_indices(obj.get("citations"))
    if not indices:
        # Recover citations from inline [N] markers when the model omitted the
        # structured field but marked the prose.
        indices = _inline_citation_indices(answer)
    return answer, _citations_from_indices(indices, rows)


def _fallback_answer(rows: list[MemoryRow]) -> tuple[str, list[Citation]]:
    """Ranked-list degradation when no LLM is available or synthesis fails."""
    lines = ["No synthesis model available — most relevant memories, ranked:"]
    citations: list[Citation] = []
    for i, row in enumerate(rows, start=1):
        lines.append(f"{i}. {row.content.strip()}")
        citations.append(Citation(index=i, memory_id=row.id, row=row))
    return "\n".join(lines), citations


async def synthesize(
    query: str,
    rows: list[MemoryRow],
    *,
    llm: SynthesisLLM | None = None,
    top_n: int = DEFAULT_TOP_N,
    now: datetime | None = None,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> SearchResult:
    """Turn ranked ``rows`` into a cited answer + gap analysis + confidence.

    Uses ``llm`` for the prose when provided; degrades to a ranked list when
    it's absent, raises, or returns unparseable output. Gap analysis and
    confidence are always computed deterministically over the top-N rows.
    """
    now = now or datetime.now(timezone.utc)
    top = list(rows[:top_n])
    gaps = analyze_gaps(query, top, now=now, stale_after_days=stale_after_days)
    confidence = compute_confidence(query, top)

    if not top:
        return SearchResult(
            query=query,
            answer="No relevant memories found.",
            sources=[],
            citations=[],
            confidence=0.0,
            gaps=gaps,
            rows=list(rows),
            used_llm=False,
        )

    used_llm = False
    answer: str
    citations: list[Citation]
    if llm is not None:
        try:
            text = await llm.complete(
                system=_SYSTEM_PROMPT,
                prompt=f"Question: {query}\n\nMemories:\n{_render_rows(top)}",
            )
            answer, citations = _llm_synthesize(query, top, text)
            used_llm = True
        except Exception:  # any LLM/parse failure → graceful degradation
            answer, citations = _fallback_answer(top)
    else:
        answer, citations = _fallback_answer(top)

    sources = [c.row for c in citations]
    return SearchResult(
        query=query,
        answer=answer,
        sources=sources,
        citations=citations,
        confidence=confidence,
        gaps=gaps,
        rows=list(rows),
        used_llm=used_llm,
    )
