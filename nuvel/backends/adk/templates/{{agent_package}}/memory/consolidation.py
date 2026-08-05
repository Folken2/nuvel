"""
Periodic memory consolidation — Nuvel's store-agnostic "dream" pass.

The store-agnostic equivalent of a server-side profile-generation pass: it
reviews a user's accumulated raw memories, dedupes exact-text duplicates,
collapses near-duplicates by cosine similarity, reconciles contradictions,
and distils a compact structured profile (see ``profile.py``). No Vertex /
Gemini-specific generation — it uses the configured LLM (any callable that
maps a prompt to a JSON string) plus embeddings + cosine similarity.

Runs off the same lightweight scheduler pattern the cron subsystem uses
(``start_consolidation_scheduler`` / ``stop_consolidation_scheduler``), gated
by ``NUVEL_MEMORY_CONSOLIDATION=1`` and paced by
``NUVEL_MEMORY_CONSOLIDATION_INTERVAL`` seconds (default daily). The FastAPI
lifespan in ``run_adk.py`` starts it, mirroring the cron scheduler.

The dedupe/reconcile core is pure and dependency-free so it is directly
unit-testable; the LLM + embedder are injected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from typing import Any, Awaitable, Callable, Optional, Sequence

from . import profile as _profile

logger = logging.getLogger(__name__)

ENV_ENABLED = "NUVEL_MEMORY_CONSOLIDATION"
ENV_INTERVAL = "NUVEL_MEMORY_CONSOLIDATION_INTERVAL"
ENV_SIM_THRESHOLD = "NUVEL_MEMORY_CONSOLIDATION_SIM"

DEFAULT_INTERVAL_SECONDS = 24 * 3600.0
DEFAULT_SIM_THRESHOLD = 0.92

# prompt -> JSON string (may be sync or async)
LlmFn = Callable[[str], Any]
# text -> embedding vector (may be sync or async), or None when unavailable
EmbedFn = Callable[[str], Any]

_WS_RE = re.compile(r"\s+")


def is_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip().lower() in ("1", "true", "yes", "on")


def _interval_seconds() -> float:
    try:
        val = float(os.environ.get(ENV_INTERVAL, "") or DEFAULT_INTERVAL_SECONDS)
    except ValueError:
        return DEFAULT_INTERVAL_SECONDS
    return val if val > 0 else DEFAULT_INTERVAL_SECONDS


def _sim_threshold() -> float:
    try:
        return float(os.environ.get(ENV_SIM_THRESHOLD, "") or DEFAULT_SIM_THRESHOLD)
    except ValueError:
        return DEFAULT_SIM_THRESHOLD


# ── Pure dedupe / reconcile core ─────────────────────────────────────


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").strip().lower())


def dedupe_exact(entries: Sequence[str]) -> list[str]:
    """Drop exact-text duplicates (whitespace/case-insensitive), order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for entry in entries:
        key = _norm(entry)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(entry.strip())
    return out


# Copulas/verbs that separate a statement's subject from its predicate. The
# "topic" of a fact is its subject phrase up to and including the verb, so
# "user prefers dark mode" and "user prefers light mode" share the topic
# "user prefers" (a contradiction) while "user lives in Lisbon" does not.
_COPULAS = (
    "is", "are", "was", "were", "am", "be",
    "prefers", "likes", "loves", "hates", "wants", "needs",
    "uses", "runs", "works", "lives", "has", "owns",
    "speaks", "writes", "codes",
)


def _topic_key(entry: str) -> Optional[str]:
    """Subject+verb phrase identifying what a statement is *about* (or None).

    Pure heuristic, no model: the topic is the normalized tokens up to and
    including the first copula/verb. Entries with no recognizable verb return
    ``None`` — they are never treated as contradicting anything.
    """
    toks = _norm(entry).split()
    for i, tok in enumerate(toks):
        if tok in _COPULAS:
            return " ".join(toks[: i + 1])
    return None


def reconcile_contradictions(
    entries: Sequence[str],
) -> tuple[list[str], list[dict[str, str]]]:
    """Collapse contradictory statements about the same topic, newest wins.

    ``entries`` are assumed oldest-first (chronological). When a later entry
    shares a topic (see ``_topic_key``) with an earlier kept entry but states
    something different, the newer text replaces the stale one *in place* and
    the conflict is recorded. Returns ``(kept, conflicts)`` where each conflict
    is ``{"topic", "kept", "dropped"}``. Pure — directly unit-testable.
    """
    kept: list[str] = []
    topic_pos: dict[str, int] = {}
    conflicts: list[dict[str, str]] = []
    for entry in entries:
        text = (entry or "").strip()
        if not text:
            continue
        topic = _topic_key(text)
        if topic is not None and topic in topic_pos:
            idx = topic_pos[topic]
            prev = kept[idx]
            if _norm(prev) != _norm(text):
                conflicts.append({"topic": topic, "kept": text, "dropped": prev})
                kept[idx] = text  # newer statement wins the slot
            continue
        if topic is not None:
            topic_pos[topic] = len(kept)
        kept.append(text)
    return kept, conflicts


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 on degenerate input)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def dedupe_similar(
    entries: Sequence[str],
    embeddings: Sequence[Optional[Sequence[float]]],
    *,
    threshold: float,
) -> list[str]:
    """Collapse near-duplicate entries by cosine similarity.

    Greedy: walk entries in order, keep an entry unless it is >= ``threshold``
    similar to one already kept, in which case the *longer* text is retained
    (more specific wins). Entries without an embedding are always kept.
    """
    kept: list[str] = []
    kept_vecs: list[Optional[Sequence[float]]] = []
    for entry, vec in zip(entries, embeddings):
        if vec is None:
            kept.append(entry)
            kept_vecs.append(None)
            continue
        merged = False
        for i, kvec in enumerate(kept_vecs):
            if kvec is None:
                continue
            if cosine_similarity(vec, kvec) >= threshold:
                if len(entry) > len(kept[i]):
                    kept[i] = entry
                    kept_vecs[i] = vec
                merged = True
                break
        if not merged:
            kept.append(entry)
            kept_vecs.append(vec)
    return kept


def _coerce_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


_PROFILE_PROMPT = (
    "You are consolidating an agent's long-term memory about ONE user. Below "
    "is a deduplicated list of raw memory entries. Reconcile any contradictions "
    "(prefer the most recent / most specific statement and DROP the stale one), "
    "then produce a compact structured profile.\n\n"
    "Reply with STRICT JSON only, no markdown, matching exactly:\n"
    '{"summary": "<=2 sentence overview", "role": "user\'s role or \'\'", '
    '"interests": ["..."], "durable_facts": ["stable facts, contradictions '
    'already resolved"]}\n\n'
    "Raw memories:\n"
)


async def _call_llm(llm_fn: LlmFn, prompt: str) -> Any:
    result = llm_fn(prompt)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _call_embed(embed_fn: EmbedFn, text: str) -> Any:
    result = embed_fn(text)
    if asyncio.iscoroutine(result):
        return await result
    return result


async def consolidate_memories(
    entries: Sequence[str],
    *,
    llm_fn: LlmFn | None = None,
    embed_fn: EmbedFn | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Run a full consolidation pass over ``entries``.

    Steps: exact dedupe -> (optional) cosine-similarity dedupe -> (optional)
    LLM reconcile + profile build. ``llm_fn`` / ``embed_fn`` are injected so
    the core is testable without a live model. Returns a dict with the
    consolidated ``profile``, the ``kept`` entries, and per-stage ``stats``.
    """
    original = [e for e in entries if (e or "").strip()]
    after_exact = dedupe_exact(original)

    thr = _sim_threshold() if threshold is None else threshold
    after_similar = after_exact
    if embed_fn is not None and len(after_exact) > 1:
        vecs: list[Optional[Sequence[float]]] = []
        for entry in after_exact:
            try:
                vecs.append(await _call_embed(embed_fn, entry))
            except Exception:
                logger.warning("consolidation: embed failed", exc_info=True)
                vecs.append(None)
        after_similar = dedupe_similar(after_exact, vecs, threshold=thr)

    # Deterministic contradiction reconciliation (newest wins) before the LLM.
    reconciled, conflicts = reconcile_contradictions(after_similar)

    profile: dict[str, Any] = {}
    if llm_fn is not None and reconciled:
        prompt = _PROFILE_PROMPT + "\n".join(f"- {e}" for e in reconciled)
        if conflicts:
            prompt += "\n\nResolved contradictions (stale statements already dropped):\n"
            prompt += "\n".join(
                f"- {c['topic']}: kept {c['kept']!r}, dropped {c['dropped']!r}"
                for c in conflicts
            )
        try:
            profile = _profile.normalize_profile(
                _coerce_json(await _call_llm(llm_fn, prompt))
            )
        except Exception:
            logger.warning("consolidation: profile generation failed", exc_info=True)
            profile = {}

    return {
        "profile": profile,
        "kept": reconciled,
        "conflicts": conflicts,
        "stats": {
            "input": len(original),
            "after_exact": len(after_exact),
            "after_similar": len(after_similar),
            "after_reconcile": len(reconciled),
            "removed_exact": len(original) - len(after_exact),
            "removed_similar": len(after_exact) - len(after_similar),
            "conflicts": len(conflicts),
        },
    }


# ── Store-agnostic per-user job ──────────────────────────────────────


def _markdown_entries() -> list[str]:
    """Read raw memory entries from the markdown store (core + topics)."""
    from ..state.memory import list_topics, load_core_memory, load_topic

    entries: list[str] = []
    for blob in [load_core_memory(), *[load_topic(t) for t in list_topics()]]:
        for line in (blob or "").splitlines():
            line = line.strip()
            if line.startswith("- "):
                # strip a leading "[timestamp] " if present
                body = re.sub(r"^-\s*\[[^\]]*\]\s*", "", line).strip()
                body = body if body else line[2:].strip()
                if body:
                    entries.append(body)
    return entries


async def run_consolidation_job(
    *,
    user_id: str | None = None,
    entries: Sequence[str] | None = None,
    llm_fn: LlmFn | None = None,
    embed_fn: EmbedFn | None = None,
) -> dict[str, Any]:
    """Consolidate one user's memories and persist the resulting profile.

    ``entries`` defaults to the markdown store. Returns the consolidation
    result dict (also written to ``profile.py``'s per-user store).
    """
    raw = list(entries) if entries is not None else _markdown_entries()
    if not raw:
        return {"profile": {}, "kept": [], "stats": {"input": 0}}
    result = await consolidate_memories(
        raw, llm_fn=llm_fn, embed_fn=embed_fn
    )
    if result["profile"]:
        try:
            _profile.save_user_profile(result["profile"], user_id=user_id)
        except Exception:
            logger.warning("consolidation: failed to save profile", exc_info=True)
    return result


# ── Scheduler (mirrors cron/scheduler.py start/stop/loop) ────────────


class _Handle:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None


_HANDLE = _Handle()


async def _loop(job: Callable[[], Awaitable[Any]], interval: float) -> None:
    assert _HANDLE.stop_event is not None
    logger.info("consolidation: scheduler started (interval=%.0fs)", interval)
    while not _HANDLE.stop_event.is_set():
        try:
            await asyncio.wait_for(_HANDLE.stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if _HANDLE.stop_event.is_set():
            break
        try:
            await job()
        except Exception:
            logger.exception("consolidation: job raised")
    logger.info("consolidation: scheduler stopped")


def start_consolidation_scheduler(
    job: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Start the periodic consolidation loop. No-op unless enabled.

    ``job`` defaults to a single-user (``default``) markdown pass with no
    model wiring; deployments override it to enumerate users / wire the LLM.
    """
    if not is_enabled():
        logger.info("consolidation: NUVEL_MEMORY_CONSOLIDATION unset — disabled")
        return
    if _HANDLE.task is not None and not _HANDLE.task.done():
        return
    job = job or (lambda: run_consolidation_job())
    _HANDLE.stop_event = asyncio.Event()
    _HANDLE.task = asyncio.create_task(_loop(job, _interval_seconds()))


async def stop_consolidation_scheduler() -> None:
    if _HANDLE.task is None:
        return
    if _HANDLE.stop_event is not None:
        _HANDLE.stop_event.set()
    try:
        await asyncio.wait_for(_HANDLE.task, timeout=5.0)
    except asyncio.TimeoutError:
        _HANDLE.task.cancel()
    _HANDLE.task = None
    _HANDLE.stop_event = None


__all__ = [
    "consolidate_memories",
    "run_consolidation_job",
    "dedupe_exact",
    "dedupe_similar",
    "reconcile_contradictions",
    "cosine_similarity",
    "start_consolidation_scheduler",
    "stop_consolidation_scheduler",
    "is_enabled",
]
