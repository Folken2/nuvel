"""Meta-agent tools — file ops, scaffolding, validation, skill discovery, and Composio."""

from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool
from .skills_tools import search_skills_tool, install_skill_tool, read_skill_context_tool
from .composio_tools import list_composio_toolkits_tool


def get_tools():
    """Return all meta-agent function tools."""
    return [
        scaffold_agent_tool,
        write_file_tool,
        read_file_tool,
        list_files_tool,
        validate_agent_tool,
        search_skills_tool,
        install_skill_tool,
        read_skill_context_tool,
        list_composio_toolkits_tool,
    ]
