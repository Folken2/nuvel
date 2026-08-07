"""
Meta-Agent — creates production-ready Google ADK agents.
"""

from __future__ import annotations

import logging
import pathlib

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.skills import load_skill_from_dir
from google.adk.tools.skill_toolset import SkillToolset

from .callbacks.path_guard import path_guard
from .config import get_skills_dir, is_skill_enabled
from .config.llm import FAST_MODEL
from .plugin_bridge import (
    build_plugin_mcp_toolsets,
    build_registry,
    load_plugin_skills,
)
from .tools import get_tools
from .prompt.instructions import get_agent_instruction

logger = logging.getLogger(__name__)

load_dotenv()

_DEFAULT_SKILLS_DIR = pathlib.Path(__file__).parent / "backends" / "adk" / "skills"

#: Agent Plugin Registry, seeded once at import from the configured plugin_dirs.
#: Exposed so CLI commands (and callers) can inspect loaded plugins.
plugin_registry = build_registry()


def _build_skill_toolset() -> SkillToolset | None:
    """Load skills from the configured directory plus any discovered plugins."""
    skills_dir = get_skills_dir(_DEFAULT_SKILLS_DIR)
    skills = []
    if not skills_dir.is_dir():
        logger.warning("Skills directory not found: %s", skills_dir)
    else:
        for skill_dir in sorted(skills_dir.iterdir()):
            if not (skill_dir.is_dir() and (skill_dir / "SKILL.md").exists()):
                continue
            if not is_skill_enabled(skill_dir.name):
                logger.info("Skipping disabled skill: %s", skill_dir.name)
                continue
            try:
                skill = load_skill_from_dir(skill_dir)
                skills.append(skill)
                logger.info("Loaded skill: %s", skill.name)
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", skill_dir.name, e)

    # Merge in skills contributed by Agent Plugins.
    plugin_skills = load_plugin_skills(plugin_registry)
    if plugin_skills:
        skills.extend(plugin_skills)
        logger.info("Loaded %d plugin skills", len(plugin_skills))

    if not skills:
        logger.warning("No skills loaded")
        return None

    logger.info("Loaded %d skills", len(skills))
    return SkillToolset(skills=skills)


def _build_tools():
    """Build agent tools: file ops + SkillToolset + plugin MCP toolsets."""
    tools = list(get_tools())
    skill_toolset = _build_skill_toolset()
    if skill_toolset:
        tools.append(skill_toolset)
    tools.extend(build_plugin_mcp_toolsets(plugin_registry))
    return tools


root_agent = LlmAgent(
    model=FAST_MODEL,
    name="nuvel",
    description="Creates production-ready Google ADK agents from natural language descriptions",
    instruction=get_agent_instruction,
    tools=_build_tools(),
    before_tool_callback=path_guard,
)
