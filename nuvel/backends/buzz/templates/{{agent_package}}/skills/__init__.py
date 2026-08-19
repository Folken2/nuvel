"""Skills for {{agent_name}}, exposed to the model as tools.

A skill is a directory holding a ``SKILL.md`` in the Anthropic skills format:
YAML frontmatter (``name`` + ``description``) followed by the body. Only the
frontmatter is loaded up front — the model reads a body on demand via
``read_skill``. That progressive disclosure is the point: fifty skills cost a
few hundred prompt tokens until one is actually needed.

Drop a skill in as ``skills/<slug>/SKILL.md`` and it shows up on the next run;
no wiring needed. Set ``BUZZ_SKILLS_DIR`` to load from somewhere else.

(The ADK backend gets this for free from ADK's ``SkillToolset``. A Buzz agent
has no framework underneath it, so the two tools are defined here.)
"""

from __future__ import annotations

import os
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent

MAX_SKILL_BYTES = 100_000


def skills_root() -> Path:
    override = os.getenv("BUZZ_SKILLS_DIR")
    return Path(override).expanduser().resolve() if override else SKILLS_DIR


def _parse_frontmatter(text: str) -> dict:
    """Pull ``name``/``description`` out of a SKILL.md's YAML frontmatter.

    Deliberately a two-key scanner rather than a YAML parse: it keeps the
    generated agent dependency-free, and the skills format only guarantees
    those two keys anyway.
    """
    if not text.startswith("---"):
        return {}
    _, _, rest = text.partition("---")
    front, sep, _ = rest.partition("---")
    if not sep:
        return {}

    meta: dict[str, str] = {}
    key: str | None = None
    for raw in front.splitlines():
        if not raw.strip():
            continue
        if raw[:1].isspace() and key:  # continuation of a folded value
            meta[key] = f"{meta[key]} {raw.strip()}".strip()
            continue
        name, sep_, value = raw.partition(":")
        if not sep_:
            continue
        key = name.strip()
        meta[key] = value.strip().strip("'\"")
    return meta


def discover_skills() -> list[dict]:
    """Return ``[{slug, name, description, path}]`` for every skill on disk."""
    root = skills_root()
    if not root.is_dir():
        return []

    found: list[dict] = []
    for entry in sorted(root.iterdir()):
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        found.append(
            {
                "slug": entry.name,
                "name": meta.get("name", entry.name),
                "description": " ".join(meta.get("description", "").split()),
                "path": str(skill_md),
            }
        )
    return found


def list_skills() -> dict:
    """List every skill available to this agent, with its description."""
    skills = discover_skills()
    return {
        "status": "ok",
        "count": len(skills),
        "skills": [
            {"slug": s["slug"], "name": s["name"], "description": s["description"]}
            for s in skills
        ],
    }


def read_skill(slug: str) -> dict:
    """Read the full body of one skill by slug."""
    root = skills_root()
    target = (root / slug / "SKILL.md").resolve()

    # Keep a model-supplied slug from walking out of the skills directory.
    if not str(target).startswith(str(root) + os.sep):
        return {"status": "error", "message": f"Unknown skill: {slug}"}
    if not target.is_file():
        available = [s["slug"] for s in discover_skills()]
        return {
            "status": "error",
            "message": f"Unknown skill: {slug}. Available: {', '.join(available) or 'none'}",
        }

    content = target.read_text(encoding="utf-8")
    truncated = len(content.encode("utf-8")) > MAX_SKILL_BYTES
    if truncated:
        content = content.encode("utf-8")[:MAX_SKILL_BYTES].decode("utf-8", "ignore")
    return {"status": "ok", "slug": slug, "content": content, "truncated": truncated}


def skill_tools() -> list:
    """The skill tools, as :class:`~{{agent_package}}.agent.Tool` objects."""
    from ..agent import Tool  # imported here: agent.py imports this module

    return [
        Tool(
            name="list_skills",
            description=(
                "List the skills available to you — each is a focused guide "
                "with instructions for a specific kind of task. Call this "
                "before starting unfamiliar work."
            ),
            parameters={"type": "object", "properties": {}},
            handler=list_skills,
        ),
        Tool(
            name="read_skill",
            description=(
                "Read the full text of one skill, by its slug from list_skills. "
                "Follow its instructions for the task at hand."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Skill slug, e.g. 'incident-triage'.",
                    }
                },
                "required": ["slug"],
            },
            handler=read_skill,
        ),
    ]
