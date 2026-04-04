"""Meta-agent tools — file ops, scaffolding, and validation."""

from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool


def get_tools():
    """Return all meta-agent function tools."""
    return [
        scaffold_agent_tool,
        write_file_tool,
        read_file_tool,
        list_files_tool,
        validate_agent_tool,
    ]
