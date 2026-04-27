"""Validate tool — checks a generated agent's structure for correctness."""

from __future__ import annotations

import os
import re

from google.adk.tools import FunctionTool

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")

_ROOT_FILES = ["run_adk.py", "requirements.txt"]

_PACKAGE_FILES = [
    "__init__.py",
    "agent.py",
    "config/__init__.py",
    "config/llm.py",
    "config/logging.py",
    "plugins/__init__.py",
    "prompt/__init__.py",
    "prompt/instructions.py",
    "tools/__init__.py",
]

_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")


# ── Impl (no ToolContext) ──────────────────────────────────────────────


def _validate_agent_impl(agent_dir: str) -> dict:
    """Validate the structure of a scaffolded agent directory.

    Returns a dict with ``status``, ``errors``, and ``warnings``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Directory exists
    if not os.path.isdir(agent_dir):
        return {
            "status": "error",
            "agent_dir": agent_dir,
            "package": None,
            "errors": [f"Agent directory does not exist: {agent_dir}"],
            "warnings": [],
        }

    # 2. Root files
    for fname in _ROOT_FILES:
        if not os.path.isfile(os.path.join(agent_dir, fname)):
            errors.append(f"Missing root file: {fname}")

    # 3. Find package directory (the one containing agent.py)
    package_dir: str | None = None
    package_name: str | None = None
    for entry in os.listdir(agent_dir):
        candidate = os.path.join(agent_dir, entry)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "agent.py")):
            package_dir = candidate
            package_name = entry
            break

    if package_dir is None:
        errors.append("No package directory with agent.py found")
        return {
            "status": "error",
            "agent_dir": agent_dir,
            "package": None,
            "errors": errors,
            "warnings": warnings,
        }

    # 4. Required package files
    for rel in _PACKAGE_FILES:
        full = os.path.join(package_dir, rel)
        if not os.path.isfile(full):
            errors.append(f"Missing package file: {package_name}/{rel}")

    # 5. Scan for unresolved placeholders
    for dirpath, _dirnames, filenames in os.walk(agent_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in (".py", ".md", ".txt"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                content = open(fpath, encoding="utf-8").read()
            except Exception:
                continue
            matches = _PLACEHOLDER_RE.findall(content)
            if matches:
                rel = os.path.relpath(fpath, agent_dir)
                errors.append(f"Unresolved placeholders in {rel}: {matches}")

    # 6. Skills directories should have SKILL.md
    skills_dir = os.path.join(package_dir, "skills")
    if os.path.isdir(skills_dir):
        for entry in os.listdir(skills_dir):
            skill_path = os.path.join(skills_dir, entry)
            if os.path.isdir(skill_path):
                if not os.path.isfile(os.path.join(skill_path, "SKILL.md")):
                    warnings.append(f"Skill directory {entry}/ missing SKILL.md")

    # 7. SOUL.md should exist (warning, not error)
    soul_file = os.path.join(package_dir, "soul", "SOUL.md")
    if not os.path.isfile(soul_file):
        warnings.append("Missing soul/SOUL.md — agent has no identity layer")

    status = "ok" if not errors else "error"
    return {
        "status": status,
        "agent_dir": agent_dir,
        "package": package_name,
        "errors": errors,
        "warnings": warnings,
    }


# ── Wrapped function ──────────────────────────────────────────────────


def validate_agent(name: str, tool_context=None) -> dict:
    """Validate a generated agent's directory structure and files."""
    if tool_context is not None:
        output_dir = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
    else:
        output_dir = _OUTPUT_DIR
    agent_dir = os.path.join(output_dir, name)
    return _validate_agent_impl(agent_dir)


validate_agent_tool = FunctionTool(func=validate_agent)
