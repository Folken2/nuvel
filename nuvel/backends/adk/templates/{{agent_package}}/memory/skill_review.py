"""
Skill read + propose tools for the review fork.

The judge fork is allowed to *read* the current skill catalog and to
*propose* new or patched skills — but never to auto-author them. Proposals
are written to the same review directory the ``SkillCuratorPlugin`` uses
(``NUVEL_SKILL_PROPOSALS_DIR``, default ``~/.nuvel/skill-proposals``), so the
two loops complement rather than duplicate each other:

* ``SkillCuratorPlugin`` proposes from run-trajectory heuristics (tool churn,
  repeated errors) at Runner scope.
* the review fork proposes from the *conversation* — a durable capability the
  dialog revealed — at after-turn scope.

Both land in one human-review queue. Self-contained (no dependency on the
``--persona`` overlay's ``skill_tools``) so it works in every generated
agent.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from google.adk.tools import FunctionTool

from ..config.paths import skills_dir
from ..plugins.skill_curator_plugin import _proposals_dir

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "skill").lower()).strip("-")
    return s or "skill"


def list_skills() -> dict:
    """List the names of skills the agent currently has."""
    base = skills_dir()
    if not base.is_dir():
        return {"status": "ok", "skills": []}
    names = [
        d.name for d in sorted(base.iterdir())
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]
    return {"status": "ok", "skills": names, "count": len(names)}


def read_skill(name: str) -> dict:
    """Read a skill's SKILL.md body by name.

    Args:
        name: Skill slug (will be slugified).
    """
    slug = _slugify(name)
    path = skills_dir() / slug / "SKILL.md"
    if not path.is_file():
        return {"status": "error", "message": f"Skill '{slug}' not found."}
    return {"status": "ok", "name": slug, "content": path.read_text(encoding="utf-8")}


def propose_skill(name: str, rationale: str, body: str, action: str = "propose_new") -> dict:
    """Propose a NEW skill or a PATCH to an existing one for human review.

    Does NOT change the live skill catalog — writes a markdown proposal to the
    review queue (``NUVEL_SKILL_PROPOSALS_DIR``). A human applies it later.

    Args:
        name: Short kebab-case skill name.
        rationale: One paragraph — why this capability is worth a skill.
        body: The proposed SKILL.md body (or patch instructions).
        action: "propose_new" or "patch_existing".

    Returns:
        Status dict with the proposal path.
    """
    slug = _slugify(name)
    action = action if action in ("propose_new", "patch_existing") else "propose_new"
    out_dir = _proposals_dir()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{ts}-review-{slug}.md"
        path.write_text(
            f"---\n"
            f"action: {action}\n"
            f"skill_name: {slug}\n"
            f"source: review_fork\n"
            f"timestamp: {ts}\n"
            f"---\n\n"
            f"# Review-fork skill proposal: {slug}\n\n"
            f"**Action:** `{action}`\n\n"
            f"## Rationale\n\n{rationale.strip()}\n\n"
            f"## Proposed body / patch\n\n{body.strip()}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("propose_skill: failed to write proposal: %s", exc)
        return {"status": "error", "message": str(exc)}
    logger.info("propose_skill: wrote proposal %s", path)
    return {"status": "ok", "name": slug, "action": action, "proposal": str(path)}


skill_review_tool_list = [
    FunctionTool(list_skills),
    FunctionTool(read_skill),
    FunctionTool(propose_skill),
]

SKILL_REVIEW_TOOL_NAMES = frozenset({"list_skills", "read_skill", "propose_skill"})


__all__ = ["skill_review_tool_list", "SKILL_REVIEW_TOOL_NAMES"]
