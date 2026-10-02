"""Meta-agent tools — file ops, scaffolding, validation, skill discovery, and Composio."""

from ..config import is_tool_disabled
from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool
from .skills_tools import search_skills_tool, install_skill_tool, read_skill_context_tool
from .composio_tools import list_composio_toolkits_tool
from .shell_tool import run_cli_tool


_ALL_TOOLS = {
    "scaffold_agent_tool": scaffold_agent_tool,
    "write_file_tool": write_file_tool,
    "read_file_tool": read_file_tool,
    "list_files_tool": list_files_tool,
    "validate_agent_tool": validate_agent_tool,
    "search_skills_tool": search_skills_tool,
    "install_skill_tool": install_skill_tool,
    "read_skill_context_tool": read_skill_context_tool,
    "list_composio_toolkits_tool": list_composio_toolkits_tool,
    "run_cli_tool": run_cli_tool,
}


def get_tools():
    """Return meta-agent function tools, filtered by ``META_AGENT_DISABLED_TOOLS``."""
    return [tool for name, tool in _ALL_TOOLS.items() if not is_tool_disabled(name)]
