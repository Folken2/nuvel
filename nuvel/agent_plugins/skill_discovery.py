"""Discovery of Agent Skills bundled inside a plugin's ``skills/`` directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .exceptions import ComponentDiscoveryError


@dataclass
class DiscoveredSkill:
    """A skill found under ``<plugin_root>/skills/<name>/SKILL.md``."""

    name: str
    skill_md_path: Path
    plugin_root: Path


def _is_contained(child: Path, root: Path) -> bool:
    """Return True if ``child`` resolves to a location within ``root``."""
    try:
        child_resolved = child.resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    return child_resolved == root_resolved or child_resolved.is_relative_to(
        root_resolved
    )


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse a minimal YAML frontmatter block from the top of ``text``.

    Returns a flat ``{key: value}`` mapping, or ``None`` when the document has
    no ``---`` delimited frontmatter block. Only simple ``key: value`` scalar
    lines are supported (sufficient for the Agent Skills required fields).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None

    fm: dict[str, str] = {}
    for line in lines[1:end]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip().strip('"').strip("'")
        fm[key.strip()] = value
    return fm


def _has_valid_frontmatter(skill_md: Path) -> bool:
    """A SKILL.md is valid when it has frontmatter with name + description."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return False
    fm = parse_frontmatter(text)
    if fm is None:
        return False
    return bool(fm.get("name")) and bool(fm.get("description"))


def discover_skills(plugin_root: Path) -> list[DiscoveredSkill]:
    """Discover skills under ``plugin_root / 'skills'``.

    - Missing ``skills/`` is a valid absence -> returns ``[]``.
    - ``skills/`` existing as a file is fatal for the component ->
      :class:`ComponentDiscoveryError`.
    - Only immediate child directories with a regular ``SKILL.md`` (valid
      frontmatter, contained within the plugin root) are returned. Nested
      subdirectories are *not* searched.
    """
    plugin_root = Path(plugin_root)
    skills_dir = plugin_root / "skills"

    if not skills_dir.exists():
        return []
    if not skills_dir.is_dir():
        raise ComponentDiscoveryError(
            f"'skills' exists but is not a directory: {skills_dir}"
        )

    discovered: list[DiscoveredSkill] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue

        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            # Directory without a SKILL.md is silently skipped.
            continue

        # Containment: the skill directory and its SKILL.md must resolve
        # within the plugin root (guards against escaping symlinks).
        if not _is_contained(child, plugin_root) or not _is_contained(
            skill_md, plugin_root
        ):
            continue

        if not _has_valid_frontmatter(skill_md):
            continue

        discovered.append(
            DiscoveredSkill(
                name=child.name,
                skill_md_path=skill_md,
                plugin_root=plugin_root,
            )
        )

    return discovered


__all__ = ["DiscoveredSkill", "discover_skills", "parse_frontmatter"]
