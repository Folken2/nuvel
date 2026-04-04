"""Skill discovery, adaptation, and installation tools for ADK agents."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import yaml

# ── Constants ─────────────────────────────────────────────────────────

MIN_INSTALLS = 1_000

SKILLS_API_URL = "https://skills.sh/api/search"

ADK_ALLOWED_FRONTMATTER_KEYS = frozenset({
    "name", "description", "license", "allowed-tools", "allowed_tools",
    "metadata", "compatibility",
})


# ── Helpers ───────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Normalize a skill name to valid ADK kebab-case.

    Rules:
    - Lowercase
    - Replace underscores and spaces with hyphens
    - Remove non-alphanumeric except hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate to 64 chars
    """
    result = name.lower()
    result = result.replace("_", "-").replace(" ", "-")
    result = re.sub(r"[^a-z0-9-]", "", result)
    result = re.sub(r"-{2,}", "-", result)
    result = result.strip("-")
    return result[:64]


def _parse_skill_md(content: str) -> tuple[dict, str]:
    """Parse a SKILL.md string into (frontmatter_dict, body_str).

    Raises ValueError if frontmatter delimiters are missing.
    """
    stripped = content.lstrip("\n")
    if not stripped.startswith("---"):
        raise ValueError("SKILL.md must start with '---' frontmatter delimiter")

    # Find closing delimiter (skip the opening one)
    rest = stripped[3:]
    close_idx = rest.find("\n---")
    if close_idx == -1:
        raise ValueError("Missing closing '---' frontmatter delimiter")

    yaml_text = rest[:close_idx]
    body = rest[close_idx + 4:].lstrip("\n")

    frontmatter = yaml.safe_load(yaml_text) or {}
    return frontmatter, body


def _rebuild_skill_md(frontmatter: dict, body: str) -> str:
    """Rebuild SKILL.md from dict + body."""
    yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n{body}"


# ── Adaptation pipeline ──────────────────────────────────────────────


def adapt_skill_for_adk(source_dir: str) -> tuple[str, list[str]]:
    """Transform a community skill directory into an ADK-compatible one.

    Modifies the directory in-place and returns (adapted_dir, warnings).
    """
    warnings: list[str] = []
    source = Path(source_dir)

    # 1. Find SKILL.md (case-insensitive)
    skill_md_path = None
    for f in source.iterdir():
        if f.name.lower() == "skill.md" and f.is_file():
            skill_md_path = f
            break
    if skill_md_path is None:
        raise FileNotFoundError(f"No SKILL.md found in {source_dir}")

    # 2. Parse frontmatter and body
    content = skill_md_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_skill_md(content)

    # 3. Strip invalid frontmatter keys
    stripped_keys = [k for k in frontmatter if k not in ADK_ALLOWED_FRONTMATTER_KEYS]
    for k in stripped_keys:
        del frontmatter[k]
    if stripped_keys:
        warnings.append(f"Stripped non-ADK frontmatter keys: {', '.join(stripped_keys)}")

    # 4. Fix naming — normalize name to kebab-case
    raw_name = frontmatter.get("name", source.name)
    normalized = _normalize_name(raw_name)
    frontmatter["name"] = normalized

    # 5. Validate description
    desc = frontmatter.get("description", "")
    if not desc:
        frontmatter["description"] = "Community skill (no description provided)"
        warnings.append("Added default description")
    elif len(desc) > 1024:
        frontmatter["description"] = desc[:1024]
        warnings.append("Truncated description to 1024 chars")

    # 6. Clean resources — drop scripts/ and __pycache__
    scripts_dir = source / "scripts"
    if scripts_dir.is_dir():
        shutil.rmtree(scripts_dir)
        warnings.append("Removed scripts/ directory")

    pycache_dir = source / "__pycache__"
    if pycache_dir.is_dir():
        shutil.rmtree(pycache_dir)

    # 7. Write adapted SKILL.md — ensure filename is uppercase
    new_content = _rebuild_skill_md(frontmatter, body)
    # Remove old file (may have different case)
    skill_md_path.unlink()
    (source / "SKILL.md").write_text(new_content, encoding="utf-8")

    # Rename directory if needed
    if source.name != normalized:
        new_dir = source.parent / normalized
        source.rename(new_dir)
        return str(new_dir), warnings

    return str(source), warnings
