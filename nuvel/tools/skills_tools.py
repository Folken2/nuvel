"""Skill discovery, adaptation, and installation tools for ADK agents."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import yaml
from google.adk.tools import FunctionTool

# ── Constants ─────────────────────────────────────────────────────────

MIN_INSTALLS = 1_000

SKILLS_API_URL = "https://skills.sh/api/search"

ADK_ALLOWED_FRONTMATTER_KEYS = frozenset({
    "name", "description", "license", "allowed-tools", "allowed_tools",
    "metadata", "compatibility",
})


# ── Helpers ───────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Normalize a skill name to valid ADK kebab-case.

    Rules:
    - Lowercase
    - Replace underscores and spaces with hyphens
    - Remove non-alphanumeric except hyphens
    - Collapse consecutive hyphens
    - Strip leading/trailing hyphens
    - Truncate to 64 chars
    """
    result = name.lower()
    result = result.replace("_", "-").replace(" ", "-")
    result = re.sub(r"[^a-z0-9-]", "", result)
    result = re.sub(r"-{2,}", "-", result)
    result = result.strip("-")
    return result[:64]


def _parse_skill_md(content: str) -> tuple[dict, str]:
    """Parse a SKILL.md string into (frontmatter_dict, body_str).

    Raises ValueError if frontmatter delimiters are missing.
    """
    stripped = content.lstrip("\n")
    if not stripped.startswith("---"):
        raise ValueError("SKILL.md must start with '---' frontmatter delimiter")

    # Find closing delimiter (skip the opening one)
    rest = stripped[3:]
    close_idx = rest.find("\n---")
    if close_idx == -1:
        raise ValueError("Missing closing '---' frontmatter delimiter")

    yaml_text = rest[:close_idx]
    body = rest[close_idx + 4:].lstrip("\n")

    frontmatter = yaml.safe_load(yaml_text) or {}
    return frontmatter, body


def _rebuild_skill_md(frontmatter: dict, body: str) -> str:
    """Rebuild SKILL.md from dict + body."""
    yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n{body}"


# ── Adaptation pipeline ──────────────────────────────────────────────


def adapt_skill_for_adk(source_dir: str) -> tuple[str, list[str]]:
    """Transform a community skill directory into an ADK-compatible one.

    Modifies the directory in-place and returns (adapted_dir, warnings).
    """
    warnings: list[str] = []
    source = Path(source_dir)

    # 1. Find SKILL.md (case-insensitive)
    skill_md_path = None
    for f in source.iterdir():
        if f.name.lower() == "skill.md" and f.is_file():
            skill_md_path = f
            break
    if skill_md_path is None:
        raise FileNotFoundError(f"No SKILL.md found in {source_dir}")

    # 2. Parse frontmatter and body
    content = skill_md_path.read_text(encoding="utf-8")
    frontmatter, body = _parse_skill_md(content)

    # 3. Strip invalid frontmatter keys
    stripped_keys = [k for k in frontmatter if k not in ADK_ALLOWED_FRONTMATTER_KEYS]
    for k in stripped_keys:
        del frontmatter[k]
    if stripped_keys:
        warnings.append(f"Stripped non-ADK frontmatter keys: {', '.join(stripped_keys)}")

    # 4. Fix naming — normalize name to kebab-case
    raw_name = frontmatter.get("name", source.name)
    normalized = _normalize_name(raw_name)
    frontmatter["name"] = normalized

    # 5. Validate description
    desc = frontmatter.get("description", "")
    if not desc:
        frontmatter["description"] = "Community skill (no description provided)"
        warnings.append("Added default description")
    elif len(desc) > 1024:
        frontmatter["description"] = desc[:1024]
        warnings.append("Truncated description to 1024 chars")

    # 6. Clean resources — drop scripts/ and __pycache__
    scripts_dir = source / "scripts"
    if scripts_dir.is_dir():
        shutil.rmtree(scripts_dir)
        warnings.append("Removed scripts/ directory")

    pycache_dir = source / "__pycache__"
    if pycache_dir.is_dir():
        shutil.rmtree(pycache_dir)

    # 7. Write adapted SKILL.md — ensure filename is uppercase
    new_content = _rebuild_skill_md(frontmatter, body)
    # Remove old file (may have different case)
    skill_md_path.unlink()
    (source / "SKILL.md").write_text(new_content, encoding="utf-8")

    # Rename directory if needed
    if source.name != normalized:
        new_dir = source.parent / normalized
        source.rename(new_dir)
        return str(new_dir), warnings

    return str(source), warnings


# ── Search / Discovery ──────────────────────────────────────────────

logger = logging.getLogger(__name__)


def _fetch_search_api(query: str) -> dict:
    """Call the skills.sh search API. Returns parsed JSON."""
    url = f"{SKILLS_API_URL}?q={quote_plus(query)}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_search_response(data: dict) -> list[dict]:
    """Parse the skills.sh API response.

    Filters to skills with >= MIN_INSTALLS, builds a ``package`` field
    as ``"source@skillId"``, and sorts by installs descending.

    Returns a list of dicts with keys: name, package, installs, source, url.
    """
    skills = data.get("skills", [])
    results = []
    for s in skills:
        if s.get("installs", 0) >= MIN_INSTALLS:
            results.append({
                "name": s["name"],
                "package": f"{s['source']}@{s['skillId']}",
                "installs": s["installs"],
                "source": s["source"],
                "url": f"https://skills.sh/skills/{s['id']}",
            })
    results.sort(key=lambda x: x["installs"], reverse=True)
    return results


def search_skills(query: str, tool_context=None) -> dict:
    """Search for community skills on skills.sh.

    Returns skills with 1,000+ installs matching the query.
    """
    try:
        data = _fetch_search_api(query)
        skills = _parse_search_response(data)
        if skills:
            return {
                "status": "ok",
                "message": f"Found {len(skills)} skill(s) matching '{query}'.",
                "skills": skills,
                "query": query,
            }
        return {
            "status": "ok",
            "message": f"No skills found matching '{query}' with >= {MIN_INSTALLS} installs.",
            "skills": [],
            "query": query,
        }
    except (URLError, OSError, ValueError) as exc:
        logger.warning("skills.sh API error: %s", exc)
        return {
            "status": "error",
            "message": f"skills.sh API error: {exc}",
        }


search_skills_tool = FunctionTool(func=search_skills)


# ── Install / Read-context ──────────────────────────────────────────

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


def _check_installs(package: str) -> bool:
    """Return True if *package* has >= MIN_INSTALLS on skills.sh."""
    try:
        skill_name = package.split("@", 1)[-1] if "@" in package else package
        data = _fetch_search_api(skill_name)
        for s in data.get("skills", []):
            if s.get("skillId") == skill_name or s.get("name") == skill_name:
                return s.get("installs", 0) >= MIN_INSTALLS
        return False
    except Exception:
        return False


def _download_skill(package: str) -> str:
    """Download a skill via ``npx skills add`` and return the SKILL.md directory.

    *package* is ``"owner/repo@skill-name"``.
    Raises ``RuntimeError`` on any failure.
    """
    if "@" not in package:
        raise RuntimeError(f"Invalid package format (expected owner/repo@skill-name): {package}")

    repo, skill_name = package.rsplit("@", 1)
    tmp_dir = tempfile.mkdtemp(prefix="skill_dl_")

    try:
        subprocess.run(
            ["npx", "skills", "add", repo, "-y", "--copy", "-s", skill_name],
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except FileNotFoundError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("npx is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("npx skills add timed out after 120s")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"npx skills add failed: {exc.stderr or exc.stdout}")

    # Walk tmp_dir looking for SKILL.md
    for root, _dirs, files in os.walk(tmp_dir):
        for fname in files:
            if fname.upper() == "SKILL.MD":
                return root

    shutil.rmtree(tmp_dir, ignore_errors=True)
    raise RuntimeError(f"No SKILL.md found after downloading {package}")


def install_skill(package: str, agent_name: str, tool_context=None) -> dict:
    """Download a community skill, adapt it for ADK, and install into a generated agent.

    Only skills with 1,000+ installs are allowed (security threshold).
    """
    # 1. Check installs threshold
    if not _check_installs(package):
        return {
            "status": "error",
            "message": (
                f"Skill '{package}' does not meet the minimum 1,000 installs "
                "security threshold or was not found."
            ),
        }

    tmp_root = None
    try:
        # 2. Download
        skill_dir = _download_skill(package)
        # Remember the tmp root for cleanup
        tmp_root = skill_dir
        while os.path.dirname(tmp_root) != tmp_root:
            parent = os.path.dirname(tmp_root)
            if parent.startswith(tempfile.gettempdir()):
                if os.path.dirname(parent) == tempfile.gettempdir():
                    tmp_root = parent
                    break
                tmp_root = parent
            else:
                break

        # 3. Adapt for ADK
        adapted_dir, warnings = adapt_skill_for_adk(skill_dir)

        # 4. Try ADK validation (non-fatal)
        try:
            from google.adk.skills import load_skill_from_dir
            load_skill_from_dir(adapted_dir)
        except ImportError:
            pass
        except Exception as exc:
            warnings.append(f"ADK validation warning: {exc}")

        # 5. Copy to agent output
        output_dir = _OUTPUT_DIR
        agent_package = agent_name.replace("-", "_")
        if tool_context is not None:
            output_dir = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
            agent_package = tool_context.state.get("agent_package", agent_package)

        skill_basename = os.path.basename(adapted_dir)
        dest = os.path.join(
            output_dir, agent_name, agent_package, "skills", skill_basename
        )
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(adapted_dir, dest)

        return {
            "status": "ok",
            "message": f"Skill '{package}' installed to {dest}",
            "path": dest,
            "warnings": warnings,
        }
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        if tmp_root and os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root, ignore_errors=True)


install_skill_tool = FunctionTool(func=install_skill)


def read_skill_context(package: str, tool_context=None) -> dict:
    """Download a community skill and return its content as context.

    Use this to read a skill's instructions as inspiration. Does NOT install.
    """
    # 1. Check installs threshold
    if not _check_installs(package):
        return {
            "status": "error",
            "message": (
                f"Skill '{package}' does not meet the minimum 1,000 installs "
                "security threshold or was not found."
            ),
        }

    tmp_root = None
    try:
        # 2. Download
        skill_dir = _download_skill(package)
        tmp_root = skill_dir
        while os.path.dirname(tmp_root) != tmp_root:
            parent = os.path.dirname(tmp_root)
            if parent.startswith(tempfile.gettempdir()):
                if os.path.dirname(parent) == tempfile.gettempdir():
                    tmp_root = parent
                    break
                tmp_root = parent
            else:
                break

        # 3. Read SKILL.md
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        # Try case-insensitive
        if not os.path.isfile(skill_md_path):
            for f in os.listdir(skill_dir):
                if f.upper() == "SKILL.MD":
                    skill_md_path = os.path.join(skill_dir, f)
                    break

        skill_md_content = ""
        if os.path.isfile(skill_md_path):
            with open(skill_md_path, "r", encoding="utf-8") as fh:
                skill_md_content = fh.read()

        # 4. Read references/*.md
        references: dict[str, str] = {}
        refs_dir = os.path.join(skill_dir, "references")
        if os.path.isdir(refs_dir):
            for fname in sorted(os.listdir(refs_dir)):
                if fname.endswith(".md"):
                    fpath = os.path.join(refs_dir, fname)
                    with open(fpath, "r", encoding="utf-8") as fh:
                        references[fname] = fh.read()

        return {
            "status": "ok",
            "skill_md": skill_md_content,
            "references": references,
            "package": package,
        }
    except RuntimeError as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        if tmp_root and os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root, ignore_errors=True)


read_skill_context_tool = FunctionTool(func=read_skill_context)
