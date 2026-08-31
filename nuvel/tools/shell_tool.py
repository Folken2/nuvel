"""Shell tool — runs whitelisted CLI commands for the meta-agent."""

from __future__ import annotations

import logging
import shlex
import subprocess

from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

# Commands the meta-agent is allowed to run.
_ALLOWED_COMMANDS = frozenset(
    {
        "nuvel",
        "git",
        "python3",
        "python",
        "uv",
        "pip",
        "npm",
        "npx",
    }
)

_MAX_TIMEOUT = 120  # seconds


def run_cli(command: str, tool_context=None) -> dict:
    """Run a CLI command and return its output.

    Only whitelisted commands are allowed: nuvel, git, python3, python, uv,
    pip, npm, npx. Use this for:
    - `nuvel agent create <name> --with-composio --with-telegram ...`
    - `nuvel agent create --help` to discover flags
    - `git status`, `git log`
    - `pip install`, `npm install`, `uv run`

    Args:
        command: The full CLI command string, e.g.
            "nuvel agent create my-agent --description '...' --with-composio".
    """
    if not command or not command.strip():
        return {"status": "error", "message": "Empty command"}

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return {"status": "error", "message": f"Cannot parse command: {e}"}

    base_cmd = parts[0] if parts else ""
    if base_cmd not in _ALLOWED_COMMANDS:
        return {
            "status": "error",
            "message": (
                f"Command '{base_cmd}' is not allowed. "
                f"Allowed: {', '.join(sorted(_ALLOWED_COMMANDS))}"
            ),
        }

    try:
        result = subprocess.run(
            parts,
            capture_output=True,
            text=True,
            timeout=_MAX_TIMEOUT,
        )
        output = result.stdout
        if result.stderr:
            output += "\n--- stderr ---\n" + result.stderr

        return {
            "status": "success" if result.returncode == 0 else "error",
            "exit_code": result.returncode,
            "output": output,
            "message": f"Command exited with code {result.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Command timed out after {_MAX_TIMEOUT}s"}
    except FileNotFoundError:
        return {"status": "error", "message": f"Command not found: {base_cmd}"}
    except Exception as e:
        logger.exception("Shell command failed")
        return {"status": "error", "message": str(e)}


run_cli_tool = FunctionTool(func=run_cli)
