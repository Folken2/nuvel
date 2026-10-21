"""Deprecated alias for :mod:`nuvel_mcp`.

The Skills MCP server moved to the standalone ``nuvel-mcp`` package. Submodules
are aliased (not copied) so ``nuvel.mcp.server is nuvel_mcp.server``.
"""

import sys
import warnings

import nuvel_mcp
from nuvel_mcp import feedback, http_transport, installer, oauth, server, skills_loader
from nuvel_mcp import SkillsError, SkillsLoader, SkillsMCPServer, resolve_skills_dir

for _mod in (server, feedback, skills_loader, installer, http_transport, oauth):
    sys.modules[f"nuvel.mcp.{_mod.__name__.rsplit('.', 1)[1]}"] = _mod

warnings.warn(
    "nuvel.mcp is deprecated; import nuvel_mcp — removal not before 0.7",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["SkillsMCPServer", "SkillsLoader", "SkillsError", "resolve_skills_dir"]
