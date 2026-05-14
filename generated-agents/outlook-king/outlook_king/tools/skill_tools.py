"""
SKILL authoring tools — let the agent grow new capabilities.

Skills are SKILL.md files inside <SKILLS_DIR>/<slug>/. They are loaded by
the LazySkillToolset (see agent.py) which detects new files via mtime
and rebuilds on the next invocation — no process restart required.

A SKILL.md follows the agentskills.io spec:

    ---
    name: <slug>
    description: <one-line trigger>
    ---

    # Body
    Instructions for how to do the thing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from google.adk.tools import FunctionTool

from ..config.paths import skills_dir

logger = logging.getLogger(__name__)

_MAX_SKILL_SIZE = 12000


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:60]


def _skill_path(slug: str) -> Path:
    return skills_dir() / slug / "SKILL.md"


def list_skills() -> dict:
    """List all skills currently authored."""
    base = skills_dir()
    if not base.is_dir():
        return {"status": "ok", "skills": []}
    skills = []
    for d in sorted(base.iterdir()):
        if d.is_dir() and (d / "SKILL.md").is_file():
            skills.append(d.name)
    return {"status": "ok", "skills": skills, "count": len(skills)}


def read_skill(name: str) -> dict:
    """Read a skill's SKILL.md by name.

    Args:
        name: Skill slug (will be slugified).
    """
    slug = _slugify(name)
    path = _skill_path(slug)
    if not path.is_file():
        return {"status": "error", "message": f"Skill '{slug}' not found."}
    return {"status": "ok", "name": slug, "content": path.read_text(encoding="utf-8")}


def author_skill(name: str, description: str, body: str) -> dict:
    """Author a new skill — writes <SKILLS_DIR>/<slug>/SKILL.md.

    The skill becomes queryable on the next agent invocation (no restart
    needed — the LazySkillToolset rebuilds on mtime change).

    Args:
        name: Short slug-style name (e.g. "calendar-triage").
        description: One-line trigger phrase — when this skill should fire.
        body: Markdown body. Frontmatter is added automatically.

    Returns:
        Status dict.
    """
    slug = _slugify(name)
    if not slug:
        return {"status": "error", "message": "Invalid skill name."}
    description = description.strip().replace("\n", " ")
    body = body.strip()
    if not description or not body:
        return {"status": "error", "message": "description and body are required."}

    content = f"---\nname: {slug}\ndescription: {description}\n---\n\n{body}\n"
    if len(content) > _MAX_SKILL_SIZE:
        return {
            "status": "error",
            "message": f"Skill too large ({len(content)}/{_MAX_SKILL_SIZE}). Tighten it.",
        }

    path = _skill_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Authored skill '%s' (%d chars) at %s", slug, len(content), path)
    return {"status": "ok", "name": slug, "file": str(path)}


def update_skill(name: str, description: str, body: str) -> dict:
    """Rewrite an existing skill's SKILL.md (same shape as author_skill)."""
    slug = _slugify(name)
    if not _skill_path(slug).is_file():
        return {"status": "error", "message": f"Skill '{slug}' not found — use author_skill."}
    return author_skill(name, description, body)


# Note: discovery + reading is provided by the built-in
# google.adk.tools.skill_toolset.SkillToolset (list_skills, load_skill,
# load_skill_resource). Registering list_skills/read_skill here would
# duplicate those names and Gemini rejects duplicate function declarations.
skill_tool_list = [
    FunctionTool(author_skill),
    FunctionTool(update_skill),
]
