"""Zero-LLM entity-extraction tests (spec-facing candidate API).

Exercises ``nuvel.memory.extraction``'s ``extract_links`` /
``extract_entity_names`` / ``dedup_candidates`` / ``resolve_entity`` — the
knowledge-graph Part-1 surface. Pure Python, no database. The Postgres schema
migration is checked by asserting the shipped SQL file's content (also DB-free).
"""

from __future__ import annotations

import re
from pathlib import Path

from nuvel.memory.extraction import (
    EntityLinkCandidate,
    dedup_candidates,
    extract_entity_names,
    extract_links,
    resolve_entity,
)

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "nuvel"
    / "memory"
    / "backends"
    / "migrations"
    / "0002_entity_links.sql"
)


def _by_rel(text: str) -> dict[str, EntityLinkCandidate]:
    return {c.relationship: c for c in extract_links(text)}


def test_founder_pattern():
    links = extract_links("Alice founded Acme")
    assert len(links) == 1
    edge = links[0]
    assert edge.subject == "Alice"
    assert edge.relationship == "founded"
    assert edge.target == "Acme"
    assert edge.subject_type == "person"
    assert edge.target_type == "company"


def test_ceo_pattern():
    links = extract_links("Bob is the CEO of Corp")
    assert len(links) == 1
    edge = links[0]
    assert edge.subject == "Bob"
    assert edge.relationship == "founded"
    assert edge.target == "Corp"
    assert edge.metadata.get("title") == "CEO"


def test_works_at():
    edge = _by_rel("Charlie works at Google")["works_at"]
    assert edge.subject == "Charlie"
    assert edge.target == "Google"


def test_invested_in():
    edge = _by_rel("Sequoia invested in Stripe")["invested_in"]
    assert edge.subject == "Sequoia"
    assert edge.target == "Stripe"


def test_acquired():
    edge = _by_rel("Meta acquired Instagram")["acquired"]
    assert edge.subject == "Meta"
    assert edge.target == "Instagram"
    assert edge.confidence >= 0.85


def test_no_false_positives():
    assert extract_links("This is a sentence") == []


def test_dedup():
    # The same edge stated twice collapses to one, keeping highest confidence.
    dup = [
        EntityLinkCandidate("Alice", "person", "Acme", "company", "founded", 0.7),
        EntityLinkCandidate("Alice", "person", "Acme", "company", "founded", 0.9),
    ]
    out = dedup_candidates(dup)
    assert len(out) == 1
    assert out[0].confidence == 0.9


def test_multiple_entities():
    links = extract_links("Alice founded Acme. Bob advises Acme.")
    pairs = {(c.subject, c.relationship, c.target) for c in links}
    assert ("Alice", "founded", "Acme") in pairs
    assert ("Bob", "advises", "Acme") in pairs
    assert len(links) == 2


def test_entity_name_extraction():
    # Capitalized entities that are not sentence-initial get extracted.
    names = extract_entity_names("We met with Acme Corp and Globex yesterday.")
    assert "Acme Corp" in names
    assert "Globex" in names
    # Sentence-initial "We" is grammar, not an entity.
    assert "We" not in names


def test_confidence_ordering():
    # Same edge from a high-confidence verb (0.9) and the title form (0.85):
    # dedup prefers the higher-confidence candidate.
    links = extract_links("Alice founded Acme. Alice is the CEO of Acme.")
    founded = [c for c in links if c.relationship == "founded"]
    assert len(founded) == 1
    assert founded[0].confidence == 0.9


def test_special_characters():
    # Hyphens, apostrophes, and internal dots survive extraction.
    links = extract_links("Jean-Paul O'Brien founded Acme.io")
    assert len(links) == 1
    assert links[0].subject == "Jean-Paul O'Brien"
    assert links[0].target == "Acme.io"

    edge = _by_rel("AT&T acquired NextGen")["acquired"]
    assert edge.subject == "AT&T"


def test_schema_migration_exists():
    assert MIGRATION.exists(), "0002_entity_links.sql migration is missing"
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    # Both graph tables, created idempotently.
    assert "create table if not exists entity_links" in sql
    assert "create table if not exists entity_names" in sql

    # Spec-required entity_links columns.
    for col in (
        "source_memory_id",
        "target_entity_type",
        "target_entity_name",
        "target_entity_name_raw",
        "relationship_type",
        "confidence",
        "created_at",
        "metadata",
    ):
        assert col in sql, f"entity_links.{col} missing from migration"

    # Cascade + fuzzy-match index.
    assert "on delete cascade" in sql
    assert "gin_trgm_ops" in sql

    # Spec-required entity_names columns.
    for col in ("canonical_name", "entity_type", "aliases"):
        assert col in sql, f"entity_names.{col} missing from migration"

    # Idempotent index creation.
    assert re.search(r"create index if not exists", sql)
