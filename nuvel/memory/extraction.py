"""Zero-LLM entity + typed-relationship extraction for the knowledge graph.

Every memory write runs :func:`extract_entity_links` over the content text so
the graph *self-wires* from prose — no LLM calls, pure regex/heuristics. This is
a Python reimplementation of the *shape* of gbrain's ``link-extraction.ts``
(``inferLinkType`` verb regexes + bare-mention scan), adapted from gbrain's
markdown/wikilink world to Nuvel's plain-text memories: instead of resolving
pre-linked ``[[slug]]`` references, we capture capitalized entity phrases around
typed relationship verbs directly from the sentence.

Precedence mirrors gbrain: founded > invested_in > advises > works_at, plus the
partner/competitor/attended edges. Entities appearing in a typed edge are not
re-emitted as bare ``mentioned`` rows. Everything here is pure and side-effect
free so it unit-tests without a database; persistence lives in the store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

# Confidence tiers. Explicit verb edges are high-precision; the "is <Title> at"
# employment form is slightly softer (title parsing is fuzzier); bare mentions
# are low-confidence recall signal only.
CONF_TYPED = 0.9
CONF_TITLE = 0.85
CONF_MENTION = 0.4

# The typed edge vocabulary the graph understands (query side must stay a subset
# — see nuvel.memory.relational). Kept small and auditable.
KNOWN_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        "founded",
        "works_at",
        "invested_in",
        "advises",
        "attended",
        "partner_of",
        "competitor_of",
        "mentioned",
        "built",
        "uses",
        "reports_to",
        "acquired",
        "funded",
    }
)

# A capitalized entity phrase: one or more Capitalized tokens (allowing internal
# ``& . -`` and digits so "Acme Corp", "AT&T", "Delta Systems", "Y2K Inc" match).
_ENTITY = r"[A-Z][A-Za-z0-9&.\-']*(?:\s+[A-Z][A-Za-z0-9&.\-']*)*"

# Sentence-initial / generic capitalized words that are not proper entities.
# A phrase that reduces to one of these (after stripping a leading article) is
# dropped — precision-first, mirroring gbrain's STOPWORD_SEEDS.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "this", "that", "these", "those", "it", "he", "she",
        "they", "we", "you", "i", "when", "where", "what", "who", "why", "how",
        "if", "but", "and", "or", "so", "our", "their", "his", "her", "its",
        "there", "here", "then", "also", "however", "meanwhile", "today",
        "yesterday", "tomorrow", "everyone", "someone", "anyone", "nobody",
    }
)

_LEADING_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class EntityLink:
    """One extracted edge: ``subject --relationship--> obj`` (obj None for a
    bare mention). ``metadata`` carries optional context like ``{'position':
    'CTO'}``. Types are best-effort role heuristics (person/company/org)."""

    subject: str
    subject_type: str
    relationship: str
    obj: str | None
    obj_type: str | None
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ── typed relationship patterns ────────────────────────────────────────────
#
# Each (compiled regex, relationship, subject_type, obj_type, confidence). The
# regexes capture ``subj`` and ``obj`` named groups. Ordered by precedence so
# the first match for a given (subject, object) pair wins the type.

_TITLE = r"(?P<title>[A-Z][A-Za-z]*(?:\s+[A-Za-z]+){0,3}?)"


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_PATTERNS: list[tuple[re.Pattern[str], str, str, str, float]] = [
    # founded — "Alice founded Acme", "Alice co-founded Acme", "Alice started Acme"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:founded|co-?founded|started)\s+(?P<obj>{_ENTITY})"),
     "founded", "person", "company", CONF_TYPED),
    # invested_in — "Sequoia invested in Acme", "X funded Acme", "X backed Acme"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:invested in|invests in|funded|backed|led the round in)\s+(?P<obj>{_ENTITY})"),
     "invested_in", "company", "company", CONF_TYPED),
    # advises — "Carol advises Delta"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:advises|advised|is an advisor to|is advisor to)\s+(?P<obj>{_ENTITY})"),
     "advises", "person", "company", CONF_TYPED),
    # partner_of — "Acme partnered with Globex", "Acme partners with Globex"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:partnered with|partners with|teamed up with)\s+(?P<obj>{_ENTITY})"),
     "partner_of", "company", "company", CONF_TYPED),
    # competitor_of — "Acme competes with Beta", "Acme is a competitor of Beta"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:competes with|is a competitor of|rivals)\s+(?P<obj>{_ENTITY})"),
     "competitor_of", "company", "company", CONF_TYPED),
    # attended — "Dan attended Stanford", "Dan studied at MIT", "Dan graduated from Yale"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:attended|studied at|graduated from)\s+(?P<obj>{_ENTITY})"),
     "attended", "person", "org", CONF_TYPED),
    # works_at (plain) — "Dana works at Initech", "Dana worked at Initech for X"
    (_p(rf"(?P<subj>{_ENTITY})\s+(?:works at|worked at|works for|joined)\s+(?P<obj>{_ENTITY})"),
     "works_at", "person", "company", CONF_TYPED),
]

# "Bob is CTO at Globex" — captures a title into metadata. Handled separately so
# it can attach the position; slightly lower confidence than an explicit verb.
_WORKS_AT_TITLE = _p(
    rf"(?P<subj>{_ENTITY})\s+is\s+(?:an?\s+|the\s+)?{_TITLE}\s+(?:at|of)\s+(?P<obj>{_ENTITY})"
)


def normalize_entity_name(name: str) -> str:
    """Canonical key for entity resolution / dedup: lowercase, article-stripped,
    punctuation-trimmed, whitespace-collapsed. ``"The Acme Corp."`` → ``"acme
    corp"``. Mirrors gbrain's ``normalizeBasename`` role."""
    s = _LEADING_ARTICLE.sub("", name.strip())
    s = s.strip(" \t\r\n.,;:!?\"'`()[]")
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _clean_entity(raw: str) -> str | None:
    """Strip a leading article, trim; drop if it reduces to a stopword/empty."""
    s = _LEADING_ARTICLE.sub("", raw.strip()).strip(" \t\r\n.,;:!?\"'`")
    s = re.sub(r"\s+", " ", s)
    if not s:
        return None
    if s.lower() in _STOPWORDS:
        return None
    return s


def extract_entity_links(content: str) -> list[EntityLink]:
    """Extract typed edges + bare mentions from ``content``.

    Zero LLM. Typed edges come from the verb patterns above; every remaining
    capitalized entity phrase not already an endpoint of a typed edge becomes a
    low-confidence ``mentioned`` row (recall signal). Duplicate edges (same
    normalized subject/relationship/object) collapse, highest confidence kept.
    """
    if not content or not content.strip():
        return []

    links: list[EntityLink] = []
    # (subject_norm, relationship, obj_norm) → index in links, for dedup.
    seen: dict[tuple[str, str, str | None], int] = {}
    endpoints: set[str] = set()  # normalized names already in a typed edge

    def add(link: EntityLink) -> None:
        key = (
            normalize_entity_name(link.subject),
            link.relationship,
            normalize_entity_name(link.obj) if link.obj else None,
        )
        prev = seen.get(key)
        if prev is None:
            seen[key] = len(links)
            links.append(link)
        elif link.confidence > links[prev].confidence:
            links[prev] = link

    def _emit_typed(subj: str, rel: str, obj: str, s_type: str, o_type: str,
                    conf: float, meta: dict[str, Any]) -> None:
        s = _clean_entity(subj)
        o = _clean_entity(obj)
        if not s or not o:
            return
        endpoints.add(normalize_entity_name(s))
        endpoints.add(normalize_entity_name(o))
        add(EntityLink(s, s_type, rel, o, o_type, conf, dict(meta)))

    # 1. "is <Title> at <Company>" employment form (attaches position metadata).
    for m in _WORKS_AT_TITLE.finditer(content):
        title = (m.group("title") or "").strip()
        _emit_typed(
            m.group("subj"), "works_at", m.group("obj"), "person", "company",
            CONF_TITLE, {"position": title} if title else {},
        )

    # 2. Verb patterns, in precedence order.
    for regex, rel, s_type, o_type, conf in _PATTERNS:
        for m in regex.finditer(content):
            _emit_typed(m.group("subj"), rel, m.group("obj"), s_type, o_type, conf, {})

    # 3. Bare mentions: any capitalized phrase not already a typed endpoint.
    for m in re.finditer(_ENTITY, content):
        name = _clean_entity(m.group(0))
        if not name:
            continue
        norm = normalize_entity_name(name)
        if norm in endpoints:
            continue
        add(EntityLink(name, "unknown", "mentioned", None, None, CONF_MENTION, {}))

    return links


# ─────────────────────────────────────────────────────────────────────────────
# Spec-facing public API (candidate model)
#
# A second, self-contained view over the same zero-LLM idea, exposed under the
# names the knowledge-graph spec asks for: :func:`extract_links` returns *typed*
# edge candidates only (no bare mentions — those come from
# :func:`extract_entity_names`), :func:`dedup_candidates` collapses duplicates
# keeping the highest confidence, and :func:`resolve_entity` fuzzy-matches a
# name against a known-names table. ``PATTERNS`` is the readable regex→spec map
# these are driven by. Still pure and DB-free.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EntityLinkCandidate:
    """A typed edge candidate: ``subject --relationship--> target``.

    ``*_raw`` fields keep the original casing from the text for display;
    ``subject``/``target`` are the same phrases (extraction preserves casing and
    normalization is applied only for dedup/resolution). ``metadata`` carries
    optional context such as ``{'title': 'CEO'}``.
    """

    subject: str
    subject_type: str
    target: str
    target_type: str
    relationship: str
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)


# Readable regex → (subject_type, target_type, relationship, confidence) map.
# Named groups ``subj``/``obj`` are the two endpoints; ``title`` (optional) is
# lifted into metadata. Uses the shared _ENTITY phrase so single-token names
# ("Alice", "Meta") and multi-token names ("Acme Corp") both match. Ordered by
# precedence — dedup keeps the highest confidence when the same edge matches
# more than one pattern.
PATTERNS: dict[str, tuple[str, str, str, float]] = {
    # founders
    rf"(?P<subj>{_ENTITY})\s+(?:co-?founded|founded|started)\s+(?P<obj>{_ENTITY})":
        ("person", "company", "founded", 0.9),
    rf"(?P<subj>{_ENTITY})\s+is\s+(?:the\s+|an?\s+)?(?P<title>(?i:founder|co-?founder|ceo|cto|cfo))\s+(?:of|at)\s+(?P<obj>{_ENTITY})":
        ("person", "company", "founded", 0.85),
    # employment
    rf"(?P<subj>{_ENTITY})\s+works?\s+(?:at|for)\s+(?P<obj>{_ENTITY})":
        ("person", "company", "works_at", 0.8),
    rf"(?P<subj>{_ENTITY})\s+(?:joined|runs|leads?|heads?)\s+(?P<obj>{_ENTITY})":
        ("person", "company", "works_at", 0.75),
    rf"(?P<subj>{_ENTITY})\s+reports?\s+to\s+(?P<obj>{_ENTITY})":
        ("person", "person", "reports_to", 0.8),
    # investing / funding
    rf"(?P<subj>{_ENTITY})\s+invested\s+in\s+(?P<obj>{_ENTITY})":
        ("company", "company", "invested_in", 0.85),
    rf"(?P<subj>{_ENTITY})\s+(?:funded|backed)\s+(?P<obj>{_ENTITY})":
        ("company", "company", "funded", 0.7),
    # acquisitions
    rf"(?P<subj>{_ENTITY})\s+acquir(?:ed|ing)\s+(?P<obj>{_ENTITY})":
        ("company", "company", "acquired", 0.9),
    # partnerships
    rf"(?P<subj>{_ENTITY})\s+(?:partnered|teamed up|collaborated)\s+with\s+(?P<obj>{_ENTITY})":
        ("org", "org", "partner_of", 0.8),
    # advisory
    rf"(?P<subj>{_ENTITY})\s+advis(?:es|ed)\s+(?P<obj>{_ENTITY})":
        ("person", "company", "advises", 0.8),
    # education
    rf"(?P<subj>{_ENTITY})\s+(?:attended|studied at|graduated from)\s+(?P<obj>{_ENTITY})":
        ("person", "institution", "attended", 0.85),
    # build / usage
    rf"(?P<subj>{_ENTITY})\s+built\s+(?P<obj>{_ENTITY})":
        ("org", "product", "built", 0.75),
    rf"(?P<subj>{_ENTITY})\s+uses?\s+(?P<obj>{_ENTITY})":
        ("org", "technology", "uses", 0.6),
}

# Compile once, preserving declaration (precedence) order.
_SPEC_PATTERNS: list[tuple[re.Pattern[str], str, str, str, float]] = [
    (re.compile(pat), s_type, o_type, rel, conf)
    for pat, (s_type, o_type, rel, conf) in PATTERNS.items()
]


def _spec_entity(raw: str, *, tail: bool = False) -> str | None:
    """Clean a captured endpoint, first splitting at sentence boundaries so a
    greedy match can't glue two sentences together. The subject keeps the
    segment *after* the last break (``tail=True``: "Acme. Bob" → "Bob"); the
    object keeps the segment *before* the first break ("Acme. Bob" → "Acme").
    The dot stays legal *inside* a token ("Acme.io", "AT&T") — only ``. ``
    (period + whitespace) is a break."""
    parts = re.split(r"\.\s+", raw)
    return _clean_entity(parts[-1] if tail else parts[0])


def _normalize_title(raw: str) -> str:
    """Acronym titles (ceo/cto/cfo) upper-case; word titles title-case."""
    t = re.sub(r"\s+", " ", raw.strip())
    return t.upper() if len(t) <= 3 else t.title()


def extract_links(text: str) -> list[EntityLinkCandidate]:
    """Extract typed relationship candidates from ``text`` (zero LLM).

    Runs every pattern in :data:`PATTERNS`, returns deduped candidates (highest
    confidence kept per edge). Bare mentions are *not* included — use
    :func:`extract_entity_names` for those.
    """
    if not text or not text.strip():
        return []

    out: list[EntityLinkCandidate] = []
    for regex, s_type, o_type, rel, conf in _SPEC_PATTERNS:
        for m in regex.finditer(text):
            subj = _spec_entity(m.group("subj"), tail=True)
            obj = _spec_entity(m.group("obj"))
            if not subj or not obj:
                continue
            meta: dict[str, Any] = {}
            groups = m.groupdict()
            if groups.get("title"):
                meta["title"] = _normalize_title(groups["title"])
            out.append(
                EntityLinkCandidate(subj, s_type, obj, o_type, rel, conf, meta)
            )
    return dedup_candidates(out)


def dedup_candidates(
    candidates: list[EntityLinkCandidate],
) -> list[EntityLinkCandidate]:
    """Collapse duplicate edges keyed by (norm subject, relationship, norm
    target); keep the highest-confidence candidate. Insertion order preserved."""
    best: dict[tuple[str, str, str], int] = {}
    result: list[EntityLinkCandidate] = []
    for cand in candidates:
        key = (
            normalize_entity_name(cand.subject),
            cand.relationship,
            normalize_entity_name(cand.target),
        )
        idx = best.get(key)
        if idx is None:
            best[key] = len(result)
            result.append(cand)
        elif cand.confidence > result[idx].confidence:
            result[idx] = cand
    return result


def extract_entity_names(text: str) -> set[str]:
    """Bare named entities: capitalized phrases that are *not* at the very start
    of a sentence (sentence-initial capitals are usually just grammar, not
    proper nouns). Returns the raw display phrases for 'mentioned' edges."""
    if not text:
        return set()

    # Offsets that begin a sentence: text start, or after . ! ? and whitespace.
    sentence_starts: set[int] = set()
    for m in re.finditer(r"(?:^|[.!?]\s+)", text):
        sentence_starts.add(m.end())

    names: set[str] = set()
    for m in re.finditer(_ENTITY, text):
        if m.start() in sentence_starts:
            continue
        name = _clean_entity(m.group(0))
        if name:
            names.add(name)
    return names


def resolve_entity(name: str, existing_names: dict[str, str]) -> str:
    """Resolve ``name`` against a known-entity table (fuzzy).

    ``existing_names`` maps normalized name → canonical name. Returns the
    canonical name for the best match whose similarity clears the threshold,
    else the normalized form of ``name`` (a new, unresolved entity). Similarity
    is a character-level ratio — a pure-Python stand-in for the trigram distance
    the Postgres ``pg_trgm`` index provides at query time.
    """
    norm = normalize_entity_name(name)
    if not norm:
        return norm
    if norm in existing_names:
        return existing_names[norm]

    best_key: str | None = None
    best_ratio = 0.0
    for known in existing_names:
        ratio = SequenceMatcher(None, norm, known).ratio()
        if ratio > best_ratio:
            best_ratio, best_key = ratio, known
    if best_key is not None and best_ratio >= 0.85:
        return existing_names[best_key]
    return norm
