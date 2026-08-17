"""Nuvel Skills MCP server.

A stdlib-only MCP (Model Context Protocol) stdio server that exposes a Nuvel
Skills hub (a directory with ``index.json`` and ``<theme>/<name>/SKILL.md``
files) as MCP resources and tools. Started via ``nuvel mcp serve``.
"""

from nuvel.mcp.server import SkillsMCPServer
from nuvel.mcp.skills_loader import SkillsError, SkillsLoader, resolve_skills_dir

__all__ = [
    "SkillsMCPServer",
    "SkillsLoader",
    "SkillsError",
    "resolve_skills_dir",
]
