"""``SkillManager`` — discover, search and install Nuvel Hub skills.

A *skill* is a self-contained directory holding a ``SKILL.md`` (Anthropic skills
format: YAML frontmatter + Markdown body) plus any ``scripts/``/``assets/``. The
**hub** is a checkout of the Nuvel Skills Hub — a tree of ``<category>/<skill>/``
directories. Installing a skill into a bot means copying its whole directory into
that bot's Hermes profile under ``<hermes_home>/profiles/<bot>/skills/``.

Design notes
------------
* **No shell for parsing.** Frontmatter is split by hand (the ``---`` fences)
  and handed to :func:`yaml.safe_load`; we never ``exec`` a SKILL.md.
* **Depth-tolerant discovery.** Skills may sit at ``<cat>/SKILL.md`` (a category
  that is itself a skill) or nested deeper (``<cat>/<sub>/<skill>/SKILL.md``);
  every ``SKILL.md`` under the hub is discovered. ``category`` is always the
  first path component.
* **Auto-discovery + clone.** With no explicit ``hub_path`` the manager looks at
  ``$NUVEL_SKILLS_HUB``, then common sibling paths, and finally ``git clone``s
  the public hub into the user data dir.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from .errors import BotCLIError, SkillNotFoundError
from .types import InstalledSkill, SkillInfo

#: Environment variable pointing at a local skills-hub checkout.
HUB_ENV = "NUVEL_SKILLS_HUB"

#: Where an auto-cloned hub is written (and probed on later runs).
DEFAULT_HUB_HOME = Path.home() / ".local" / "share" / "nuvel" / "skills"

#: Public hub cloned when nothing local is found.
HUB_GIT_URL = "https://github.com/Folken2/skills.git"

#: Default Hermes home (its ``profiles/`` dir receives installed skills).
DEFAULT_HERMES_HOME = Path.home() / ".hermes"


@dataclass
class _SkillRecord:
    """Internal, richer view of a skill: keeps the on-disk location.

    ``ref`` is the skill directory's path relative to the hub root, POSIX-style
    (e.g. ``hr/payroll-processor``) — the canonical hub address.
    """

    name: str
    category: str
    description: str
    tags: list[str]
    version: str
    ref: str
    directory: Path

    def to_info(self) -> SkillInfo:
        return SkillInfo(
            name=self.name,
            category=self.category,
            description=self.description,
            tags=list(self.tags),
            version=self.version,
        )


def _parse_frontmatter(text: str) -> dict:
    """Return the YAML frontmatter of a SKILL.md as a dict (``{}`` if none)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _extract_tags(data: dict) -> list[str]:
    """Dig ``metadata.hermes.tags`` out of frontmatter, tolerating gaps."""
    meta = data.get("metadata")
    if not isinstance(meta, dict):
        return []
    hermes = meta.get("hermes")
    if not isinstance(hermes, dict):
        return []
    tags = hermes.get("tags")
    return [str(t) for t in tags] if isinstance(tags, list) else []


class SkillManager:
    """Discover, search and install skills from the Nuvel Skills Hub."""

    def __init__(self, hub_path: str | None = None) -> None:
        # Resolved lazily so passing an explicit path never triggers a clone,
        # and auto-discovery/cloning only happens when the hub is first used.
        self._hub_path: str | None = hub_path
        self._records: list[_SkillRecord] | None = None

    # ------------------------------------------------------------------ #
    # hub resolution
    # ------------------------------------------------------------------ #
    @property
    def hub_path(self) -> str:
        """Resolved path to the skills hub (auto-discovering/cloning if needed)."""
        if self._hub_path is None:
            self._hub_path = self._discover_hub()
        return self._hub_path

    @staticmethod
    def _discover_hub() -> str:
        """Locate a hub: env var → common paths → clone as a last resort."""
        env = os.environ.get(HUB_ENV)
        if env:
            return env
        candidates = [
            Path("../skills"),
            Path("./skills"),
            DEFAULT_HUB_HOME,
        ]
        for cand in candidates:
            if cand.is_dir():
                return str(cand)
        return SkillManager._clone_hub()

    @staticmethod
    def _clone_hub() -> str:
        """``git clone`` the public hub into :data:`DEFAULT_HUB_HOME`."""
        if not shutil.which("git"):
            raise BotCLIError(
                "cannot locate a skills hub and 'git' is not installed; set "
                f"${HUB_ENV} to a local checkout or install git to auto-clone "
                f"{HUB_GIT_URL}"
            )
        DEFAULT_HUB_HOME.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", HUB_GIT_URL, str(DEFAULT_HUB_HOME)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise BotCLIError(
                f"failed to clone skills hub from {HUB_GIT_URL}: "
                f"{(exc.stderr or '').strip()}"
            ) from exc
        return str(DEFAULT_HUB_HOME)

    # ------------------------------------------------------------------ #
    # discovery / listing
    # ------------------------------------------------------------------ #
    def _scan(self) -> list[_SkillRecord]:
        """Parse every ``SKILL.md`` under the hub (memoised per manager)."""
        if self._records is not None:
            return self._records
        root = Path(self.hub_path)
        records: list[_SkillRecord] = []
        for skill_md in sorted(root.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(root)
            parts = rel.parts
            # Skip anything under a hidden/meta directory (e.g. ``.hub``).
            if not parts or any(p.startswith(".") for p in parts):
                continue
            data = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            records.append(
                _SkillRecord(
                    name=str(data.get("name") or parts[-1]),
                    category=parts[0],
                    # Collapse block-scalar newlines so descriptions list on one line.
                    description=" ".join(str(data.get("description") or "").split()),
                    tags=_extract_tags(data),
                    version=str(data.get("version") or ""),
                    ref=rel.as_posix(),
                    directory=skill_md.parent,
                )
            )
        self._records = records
        return records

    def list_categories(self) -> list[str]:
        """Return hub category names (alphabetical, excluding hidden dirs)."""
        root = Path(self.hub_path)
        cats = {
            child.name
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        }
        return sorted(cats)

    def list_skills(self, category: str | None = None) -> list[SkillInfo]:
        """List hub skills, optionally filtered to a single ``category``."""
        records = self._scan()
        if category is not None:
            records = [r for r in records if r.category == category]
        records = sorted(records, key=lambda r: (r.category, r.name))
        return [r.to_info() for r in records]

    def search_skills(self, query: str) -> list[SkillInfo]:
        """Search by name, description or tag (case-insensitive substring)."""
        q = query.lower().strip()
        hits = []
        for r in self._scan():
            haystack = " ".join([r.name, r.description, *r.tags]).lower()
            if q in haystack:
                hits.append(r)
        hits = sorted(hits, key=lambda r: (r.category, r.name))
        return [r.to_info() for r in hits]

    # ------------------------------------------------------------------ #
    # installation
    # ------------------------------------------------------------------ #
    def install_skills(
        self,
        bot_name: str,
        skill_refs: list[str],
        hermes_home: str | None = None,
    ) -> list[InstalledSkill]:
        """Copy each referenced skill into ``bot_name``'s profile.

        ``skill_refs`` accept a full hub address (``hr/payroll-processor``) or a
        flat name (``payroll-processor``); flat names are auto-disambiguated and
        raise :class:`SkillNotFoundError` when unknown or ambiguous.
        """
        home = Path(hermes_home).expanduser() if hermes_home else DEFAULT_HERMES_HOME
        skills_root = home / "profiles" / bot_name / "skills"
        installed: list[InstalledSkill] = []
        for ref in skill_refs:
            record = self._resolve(ref)
            dest = skills_root / record.category / record.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(record.directory, dest, dirs_exist_ok=True)
            installed.append(
                InstalledSkill(name=record.name, category=record.category, path=str(dest))
            )
        return installed

    def _resolve(self, ref: str) -> _SkillRecord:
        """Resolve a skill reference to exactly one :class:`_SkillRecord`."""
        ref = ref.strip().strip("/")
        records = self._scan()
        matches: list[_SkillRecord]
        if "/" in ref:
            # Address form: match the full ref, or ``category/name`` for skills
            # that live deeper than one level.
            matches = [
                r for r in records
                if r.ref == ref or f"{r.category}/{r.name}" == ref
            ]
        else:
            # Flat name: match on the frontmatter/dir name across all categories.
            matches = [r for r in records if r.name == ref]
        if not matches:
            raise SkillNotFoundError(f"skill {ref!r} not found in hub {self.hub_path!r}")
        if len(matches) > 1:
            suggestions = ", ".join(sorted(f"{r.category}/{r.name}" for r in matches))
            raise SkillNotFoundError(
                f"skill {ref!r} is ambiguous; did you mean: {suggestions}"
            )
        return matches[0]
