"""YAML loading + cross-agent discovery of variants."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuvel.eval.replay.variant import discover_variants, load_variant

_GOOD = """\
version: friendlier-tone-1.0
name: friendlier-tone
description: warmer greeting prompt
system_prompt: |
  Hey! I'm your agent.
model: openrouter/anthropic/claude-haiku-4.5
temperature: 0.2
max_tokens: 500
"""


def test_load_variant_parses_all_fields(tmp_path: Path) -> None:
    p = tmp_path / "friendlier-tone.yaml"
    p.write_text(_GOOD, encoding="utf-8")
    v = load_variant(p)
    assert v.version == "friendlier-tone-1.0"
    assert v.name == "friendlier-tone"
    assert v.system_prompt.strip() == "Hey! I'm your agent."
    assert v.model == "openrouter/anthropic/claude-haiku-4.5"
    assert v.temperature == 0.2
    assert v.max_tokens == 500


def test_load_variant_minimal_uses_defaults(tmp_path: Path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("version: v1\nname: m\nsystem_prompt: hi\n", encoding="utf-8")
    v = load_variant(p)
    assert v.model is None
    assert v.temperature == 0.0
    assert v.max_tokens == 600


@pytest.mark.parametrize("body,msg", [
    ("name: x\nsystem_prompt: p\n", "version"),
    ("version: v1\nsystem_prompt: p\n", "name"),
    ("version: v1\nname: x\n", "system_prompt"),
    ("just a string", "mapping"),
])
def test_load_variant_missing_required_fails_fast(tmp_path: Path, body: str, msg: str) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        load_variant(p)


def test_load_variant_bad_yaml_fails_fast(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("version: v1\n  : : :\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML parse error"):
        load_variant(p)


def test_discover_variants_finds_and_filters(tmp_path: Path, monkeypatch) -> None:
    # Build generated-agents/<agent>/evals/variants/<name>.yaml for two agents.
    for agent in ("outlook-king", "ppt-king"):
        vdir = tmp_path / "generated-agents" / agent / "evals" / "variants"
        vdir.mkdir(parents=True)
        (vdir / "friendlier-tone.yaml").write_text(_GOOD, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    all_rows = discover_variants()
    assert {r.agent for r in all_rows} == {"outlook-king", "ppt-king"}
    # traces_dir is derived as the sibling of evals/, not evals/variants/
    row = next(r for r in all_rows if r.agent == "outlook-king")
    assert row.traces_dir == tmp_path / "generated-agents" / "outlook-king" / "traces"

    filtered = discover_variants(agent_filter="ppt")
    assert {r.agent for r in filtered} == {"ppt-king"}


def test_discover_variants_skips_malformed_file(tmp_path: Path, monkeypatch) -> None:
    vdir = tmp_path / "generated-agents" / "outlook-king" / "evals" / "variants"
    vdir.mkdir(parents=True)
    (vdir / "good.yaml").write_text(_GOOD, encoding="utf-8")
    (vdir / "bad.yaml").write_text(": : : corrupt", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rows = discover_variants()
    assert len(rows) == 1
    assert rows[0].variant.name == "friendlier-tone"
