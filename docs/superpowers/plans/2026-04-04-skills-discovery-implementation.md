# Skills Discovery & Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three tools (search_skills, install_skill, read_skill_context) that let the meta-agent discover, download, adapt, and install community skills from skills.sh into generated ADK agents.

**Architecture:** `search_skills` hits the skills.sh HTTP API. `install_skill` and `read_skill_context` shell out to `npx skills add` for downloading, then run an adaptation pipeline that strips invalid ADK frontmatter, fixes naming, and validates with `load_skill_from_dir`. A 1K+ installs security threshold is enforced everywhere.

**Tech Stack:** Python urllib (HTTP API), subprocess (npx CLI), google-adk skills module (validation), PyYAML (frontmatter parsing)

---

## File Structure

### New files:
- `meta_agent/tools/skills_tools.py` — Three tool functions + adaptation pipeline + FunctionTool instances
- `tests/test_skills_tools.py` — Tests for adaptation pipeline, search parsing, install count filtering

### Modified files:
- `meta_agent/tools/__init__.py:1-16` — Add three new tool imports and append to `get_tools()`
- `meta_agent/prompt/instructions.py:22-26` — Add skill discovery tools to capabilities list
- `meta_agent/prompt/instructions.py:57-67` — Add section 4b (Discover Existing Skills) to workflow
- `requirements.txt:1-5` — Add `pyyaml` dependency

---

### Task 1: ADK Adaptation Pipeline

The pure-function core that transforms a community skill directory into an ADK-compatible one. No CLI, no HTTP — just file manipulation and validation. This is the hardest part, so we build and test it first.

**Files:**
- Create: `meta_agent/tools/skills_tools.py`
- Create: `tests/test_skills_tools.py`

- [ ] **Step 1: Add pyyaml dependency**

Add `pyyaml` to `requirements.txt` and install:

```bash
# Append to requirements.txt
echo "pyyaml>=6.0,<7.0" >> requirements.txt
source .venv/bin/activate && pip install pyyaml
```

- [ ] **Step 2: Write tests for the adaptation pipeline**

Create `tests/test_skills_tools.py`:

```python
"""Tests for meta_agent.tools.skills_tools — adaptation pipeline."""

import os
import shutil
import tempfile
import textwrap

import pytest

from meta_agent.tools.skills_tools import (
    adapt_skill_for_adk,
    _parse_skill_md,
    _normalize_name,
    MIN_INSTALLS,
)


class TestNormalizeName:
    def test_already_valid(self):
        assert _normalize_name("my-skill") == "my-skill"

    def test_uppercase(self):
        assert _normalize_name("My-Skill") == "my-skill"

    def test_underscores(self):
        assert _normalize_name("my_skill") == "my-skill"

    def test_spaces(self):
        assert _normalize_name("my skill") == "my-skill"

    def test_consecutive_hyphens(self):
        assert _normalize_name("my--skill") == "my-skill"

    def test_trailing_hyphens(self):
        assert _normalize_name("my-skill-") == "my-skill"

    def test_truncate_64(self):
        long = "a" * 100
        assert len(_normalize_name(long)) <= 64


class TestParseSkillMd:
    def test_valid(self):
        content = textwrap.dedent("""\
            ---
            name: my-skill
            description: A test skill.
            ---
            Instructions here.
        """)
        fm, body = _parse_skill_md(content)
        assert fm["name"] == "my-skill"
        assert fm["description"] == "A test skill."
        assert "Instructions here." in body

    def test_extra_keys_preserved_in_parse(self):
        content = textwrap.dedent("""\
            ---
            name: my-skill
            description: A test skill.
            author: someone
            tags: [a, b]
            ---
            Body.
        """)
        fm, body = _parse_skill_md(content)
        assert fm["author"] == "someone"
        assert "Body." in body

    def test_no_frontmatter(self):
        with pytest.raises(ValueError, match="frontmatter"):
            _parse_skill_md("Just markdown, no frontmatter.")

    def test_missing_closing(self):
        with pytest.raises(ValueError, match="closing"):
            _parse_skill_md("---\nname: x\nNo closing delimiter")


class TestAdaptSkillForAdk:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_skill(self, name, frontmatter_extra="", body="Instructions.", refs=None):
        """Helper: create a skill directory with SKILL.md."""
        skill_dir = os.path.join(self.tmpdir, name)
        os.makedirs(skill_dir, exist_ok=True)
        fm = f"---\nname: {name}\ndescription: A test skill.\n{frontmatter_extra}---\n{body}\n"
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write(fm)
        if refs:
            ref_dir = os.path.join(skill_dir, "references")
            os.makedirs(ref_dir, exist_ok=True)
            for fname, content in refs.items():
                with open(os.path.join(ref_dir, fname), "w") as f:
                    f.write(content)
        return skill_dir

    def test_valid_skill_passes(self):
        src = self._make_skill("valid-skill")
        adapted_dir, warnings = adapt_skill_for_adk(src)
        assert os.path.isfile(os.path.join(adapted_dir, "SKILL.md"))
        assert warnings == []

    def test_strips_extra_frontmatter_keys(self):
        src = self._make_skill("strip-test", frontmatter_extra="author: someone\ntags: [a]\nversion: 1.0\n")
        adapted_dir, warnings = adapt_skill_for_adk(src)
        content = open(os.path.join(adapted_dir, "SKILL.md")).read()
        assert "author" not in content
        assert "tags" not in content
        assert "version" not in content
        assert "name: strip-test" in content
        assert "description: A test skill." in content
        assert len(warnings) > 0  # should warn about stripped keys

    def test_fixes_name_mismatch(self):
        # Create skill with name different from directory
        skill_dir = os.path.join(self.tmpdir, "Wrong_Name")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: Wrong_Name\ndescription: Test.\n---\nBody.\n")
        adapted_dir, warnings = adapt_skill_for_adk(skill_dir)
        # The adapted dir should have a normalized name
        skill_md = open(os.path.join(adapted_dir, "SKILL.md")).read()
        assert "name: wrong-name" in skill_md

    def test_truncates_long_description(self):
        long_desc = "x" * 1200
        src = self._make_skill("long-desc", frontmatter_extra=f"")
        # Rewrite with long description
        with open(os.path.join(src, "SKILL.md"), "w") as f:
            f.write(f"---\nname: long-desc\ndescription: {long_desc}\n---\nBody.\n")
        adapted_dir, warnings = adapt_skill_for_adk(src)
        content = open(os.path.join(adapted_dir, "SKILL.md")).read()
        # Parse out description
        lines = content.split("\n")
        desc_line = [l for l in lines if l.startswith("description:")][0]
        desc_value = desc_line.split("description:", 1)[1].strip()
        assert len(desc_value) <= 1024

    def test_drops_scripts_dir(self):
        src = self._make_skill("with-scripts")
        scripts_dir = os.path.join(src, "scripts")
        os.makedirs(scripts_dir)
        with open(os.path.join(scripts_dir, "run.py"), "w") as f:
            f.write("print('hello')")
        adapted_dir, warnings = adapt_skill_for_adk(src)
        assert not os.path.exists(os.path.join(adapted_dir, "scripts"))

    def test_keeps_references(self):
        src = self._make_skill("with-refs", refs={"guide.md": "# Guide\nContent."})
        adapted_dir, warnings = adapt_skill_for_adk(src)
        ref_file = os.path.join(adapted_dir, "references", "guide.md")
        assert os.path.isfile(ref_file)
        assert "Content." in open(ref_file).read()

    def test_adk_validation_passes(self):
        """Adapted skill must load with ADK's load_skill_from_dir."""
        src = self._make_skill("adk-valid", frontmatter_extra="author: test\n")
        adapted_dir, warnings = adapt_skill_for_adk(src)
        from google.adk.skills import load_skill_from_dir
        skill = load_skill_from_dir(adapted_dir)
        assert skill.name == "adk-valid"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills_tools.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'meta_agent.tools.skills_tools'`

- [ ] **Step 4: Implement the adaptation pipeline**

Create `meta_agent/tools/skills_tools.py` with the constants, helpers, and `adapt_skill_for_adk`:

```python
"""
Skills discovery, installation, and ADK adaptation tools.

Uses the skills.sh HTTP API for search and the `npx skills` CLI for download.
Includes an adaptation pipeline that transforms community skills (agentskills.io)
into Google ADK-compatible format (stricter validation).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import quote_plus

import yaml
from google.adk.tools import FunctionTool

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

MIN_INSTALLS = 1_000

SKILLS_API_URL = "https://skills.sh/api/search"

ADK_ALLOWED_FRONTMATTER_KEYS = frozenset({
    "name",
    "description",
    "license",
    "allowed-tools",
    "allowed_tools",
    "metadata",
    "compatibility",
})

_OUTPUT_DIR = os.getenv("AGENTS_OUTPUT_DIR", "./generated-agents")


# ── Helpers ───────────────────────────────────────────────────────────


def _normalize_name(name: str) -> str:
    """Normalize a skill name to valid ADK kebab-case."""
    # Lowercase
    name = name.lower()
    # Replace underscores and spaces with hyphens
    name = re.sub(r"[_\s]+", "-", name)
    # Remove non-alphanumeric except hyphens
    name = re.sub(r"[^a-z0-9-]", "", name)
    # Collapse consecutive hyphens
    name = re.sub(r"-{2,}", "-", name)
    # Strip leading/trailing hyphens
    name = name.strip("-")
    # Truncate to 64 chars
    if len(name) > 64:
        name = name[:64].rstrip("-")
    return name


def _parse_skill_md(content: str) -> tuple[dict, str]:
    """Parse a SKILL.md file into (frontmatter_dict, body_str).

    Raises ValueError if frontmatter is missing or malformed.
    """
    content = content.strip()
    if not content.startswith("---"):
        raise ValueError("SKILL.md missing frontmatter (must start with ---)")

    # Find closing ---
    second = content.find("---", 3)
    if second == -1:
        raise ValueError("SKILL.md missing closing frontmatter delimiter (---)")

    fm_str = content[3:second].strip()
    body = content[second + 3:].strip()

    fm = yaml.safe_load(fm_str)
    if not isinstance(fm, dict):
        raise ValueError("Frontmatter is not a YAML mapping")

    return fm, body


def _rebuild_skill_md(frontmatter: dict, body: str) -> str:
    """Rebuild a SKILL.md from frontmatter dict and body string."""
    fm_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
    return f"---\n{fm_str}\n---\n\n{body}\n"


# ── Adaptation Pipeline ──────────────────────────────────────────────


def adapt_skill_for_adk(source_dir: str) -> tuple[str, list[str]]:
    """Adapt a community skill directory for Google ADK compatibility.

    Args:
        source_dir: Path to the skill directory (must contain SKILL.md).

    Returns:
        (adapted_dir, warnings) — adapted_dir is the path to the
        modified skill directory (modified in-place), warnings is a
        list of human-readable messages about changes made.
    """
    warnings: list[str] = []

    # Find SKILL.md (case-insensitive)
    skill_md_path = None
    for fname in os.listdir(source_dir):
        if fname.lower() == "skill.md":
            skill_md_path = os.path.join(source_dir, fname)
            break

    if skill_md_path is None:
        raise FileNotFoundError(f"No SKILL.md found in {source_dir}")

    # Step 1: Parse
    content = Path(skill_md_path).read_text(encoding="utf-8")
    frontmatter, body = _parse_skill_md(content)

    # Step 2: Strip invalid frontmatter keys
    extra_keys = set(frontmatter.keys()) - ADK_ALLOWED_FRONTMATTER_KEYS
    if extra_keys:
        for key in extra_keys:
            del frontmatter[key]
        warnings.append(f"Stripped non-ADK frontmatter keys: {sorted(extra_keys)}")

    # Step 3: Fix naming
    original_name = frontmatter.get("name", "")
    normalized = _normalize_name(original_name)
    if normalized != original_name:
        warnings.append(f"Normalized name: '{original_name}' -> '{normalized}'")
        frontmatter["name"] = normalized

    # Rename directory if it doesn't match the name
    dir_name = os.path.basename(source_dir)
    if dir_name != normalized:
        new_dir = os.path.join(os.path.dirname(source_dir), normalized)
        if new_dir != source_dir:
            if os.path.exists(new_dir):
                shutil.rmtree(new_dir)
            os.rename(source_dir, new_dir)
            source_dir = new_dir
            skill_md_path = os.path.join(source_dir, os.path.basename(skill_md_path))
            warnings.append(f"Renamed directory: '{dir_name}' -> '{normalized}'")

    # Step 4: Validate description
    description = frontmatter.get("description", "")
    if not description:
        frontmatter["description"] = f"Community skill: {normalized}"
        warnings.append("Added default description (was empty)")
    elif len(description) > 1024:
        frontmatter["description"] = description[:1021] + "..."
        warnings.append(f"Truncated description from {len(description)} to 1024 chars")

    # Step 5: Clean resources — drop scripts/, keep references/ and assets/
    scripts_dir = os.path.join(source_dir, "scripts")
    if os.path.isdir(scripts_dir):
        shutil.rmtree(scripts_dir)
        warnings.append("Removed scripts/ directory (not supported by ADK)")

    # Drop __pycache__
    for dirpath, dirnames, _ in os.walk(source_dir):
        for d in dirnames:
            if d == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, d))

    # Step 6: Write adapted SKILL.md
    adapted_content = _rebuild_skill_md(frontmatter, body)
    # Ensure the file is named SKILL.md (uppercase)
    final_path = os.path.join(source_dir, "SKILL.md")
    Path(final_path).write_text(adapted_content, encoding="utf-8")
    # Remove old file if it had different casing
    if skill_md_path != final_path and os.path.exists(skill_md_path):
        os.remove(skill_md_path)

    return source_dir, warnings
```

- [ ] **Step 5: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills_tools.py -v
```

Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add meta_agent/tools/skills_tools.py tests/test_skills_tools.py requirements.txt
git commit -m "feat: add ADK adaptation pipeline for community skills

Parses SKILL.md, strips non-ADK frontmatter keys, normalizes names
to kebab-case, truncates descriptions, drops scripts/, and validates
with load_skill_from_dir. Fully tested."
```

---

### Task 2: search_skills Tool

HTTP-based search against the skills.sh API with install count filtering.

**Files:**
- Modify: `meta_agent/tools/skills_tools.py`
- Create: `tests/test_search_skills.py`

- [ ] **Step 1: Write tests**

Create `tests/test_search_skills.py`:

```python
"""Tests for search_skills — API parsing and filtering."""

import json
from unittest.mock import patch, MagicMock

import pytest

from meta_agent.tools.skills_tools import (
    _parse_search_response,
    search_skills,
    MIN_INSTALLS,
)


SAMPLE_API_RESPONSE = {
    "query": "kubernetes",
    "searchType": "fuzzy",
    "skills": [
        {"id": "microsoft/azure-skills/azure-kubernetes", "skillId": "azure-kubernetes", "name": "azure-kubernetes", "installs": 17541, "source": "microsoft/azure-skills"},
        {"id": "jeffallan/claude-skills/kubernetes-specialist", "skillId": "kubernetes-specialist", "name": "kubernetes-specialist", "installs": 5117, "source": "jeffallan/claude-skills"},
        {"id": "sickn33/skills/kubernetes-architect", "skillId": "kubernetes-architect", "name": "kubernetes-architect", "installs": 385, "source": "sickn33/skills"},
        {"id": "tiny/skills/k8s", "skillId": "k8s", "name": "k8s", "installs": 50, "source": "tiny/skills"},
    ],
    "count": 4,
    "duration_ms": 34,
}


class TestParseSearchResponse:
    def test_filters_by_min_installs(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert len(results) == 2
        assert all(r["installs"] >= MIN_INSTALLS for r in results)

    def test_includes_package_field(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert results[0]["package"] == "microsoft/azure-skills@azure-kubernetes"
        assert results[1]["package"] == "jeffallan/claude-skills@kubernetes-specialist"

    def test_sorted_by_installs_desc(self):
        results = _parse_search_response(SAMPLE_API_RESPONSE)
        assert results[0]["installs"] >= results[1]["installs"]

    def test_empty_response(self):
        results = _parse_search_response({"skills": [], "count": 0})
        assert results == []


class TestSearchSkills:
    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_returns_filtered_results(self, mock_fetch):
        mock_fetch.return_value = SAMPLE_API_RESPONSE
        result = search_skills("kubernetes")
        assert result["status"] == "ok"
        assert len(result["skills"]) == 2
        assert result["skills"][0]["name"] == "azure-kubernetes"

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_no_results(self, mock_fetch):
        mock_fetch.return_value = {"skills": [], "count": 0}
        result = search_skills("nonexistent-thing-xyz")
        assert result["status"] == "ok"
        assert result["skills"] == []
        assert "no skills found" in result["message"].lower()

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_api_error(self, mock_fetch):
        mock_fetch.side_effect = URLError("Connection refused")
        result = search_skills("test")
        assert result["status"] == "error"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_search_skills.py -v
```

Expected: FAIL — `_parse_search_response` not found

- [ ] **Step 3: Implement search_skills**

Add to `meta_agent/tools/skills_tools.py` (after the adaptation pipeline code):

```python
# ── Search API ────────────────────────────────────────────────────────


def _fetch_search_api(query: str) -> dict:
    """Call the skills.sh search API. Returns parsed JSON."""
    url = f"{SKILLS_API_URL}?q={quote_plus(query)}"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_search_response(data: dict) -> list[dict]:
    """Parse skills.sh API response, filter by MIN_INSTALLS, sort by installs desc."""
    skills = data.get("skills", [])
    results = []
    for s in skills:
        installs = s.get("installs", 0)
        if installs < MIN_INSTALLS:
            continue
        source = s.get("source", "")
        skill_id = s.get("skillId", s.get("name", ""))
        results.append({
            "name": s.get("name", ""),
            "package": f"{source}@{skill_id}",
            "installs": installs,
            "source": source,
            "url": f"https://skills.sh/{s.get('id', '')}",
        })
    results.sort(key=lambda x: x["installs"], reverse=True)
    return results


def search_skills(query: str, tool_context=None) -> dict:
    """Search for community skills on skills.sh.

    Returns skills with 1,000+ installs matching the query.
    Use this to discover existing skills before writing from scratch.

    Args:
        query: Search keywords (e.g., "kubernetes", "security review", "api integration")

    Returns:
        List of matching skills with name, package identifier, install count, and URL.
    """
    try:
        data = _fetch_search_api(query)
    except Exception as e:
        return {"status": "error", "message": f"skills.sh API error: {e}"}

    results = _parse_search_response(data)

    if not results:
        return {
            "status": "ok",
            "message": f"No skills found with 1K+ installs for '{query}'",
            "skills": [],
            "query": query,
        }

    return {
        "status": "ok",
        "message": f"Found {len(results)} skill(s) with 1K+ installs",
        "skills": results,
        "query": query,
    }


search_skills_tool = FunctionTool(func=search_skills)
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_search_skills.py -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add meta_agent/tools/skills_tools.py tests/test_search_skills.py
git commit -m "feat: add search_skills tool using skills.sh API

HTTP search with 1K+ installs security filter. Returns structured
results with package identifiers for install_skill/read_skill_context."
```

---

### Task 3: install_skill and read_skill_context Tools

Download skills via CLI, adapt for ADK, install or return as context.

**Files:**
- Modify: `meta_agent/tools/skills_tools.py`
- Create: `tests/test_install_skill.py`

- [ ] **Step 1: Write tests**

Create `tests/test_install_skill.py`:

```python
"""Tests for install_skill and read_skill_context."""

import os
import json
import shutil
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from meta_agent.tools.skills_tools import (
    install_skill,
    read_skill_context,
    _download_skill,
    _check_installs,
    MIN_INSTALLS,
)


class TestCheckInstalls:
    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_above_threshold(self, mock_fetch):
        mock_fetch.return_value = {
            "skills": [{"name": "test", "installs": 5000, "source": "owner/repo", "skillId": "test"}],
        }
        assert _check_installs("owner/repo@test") is True

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_below_threshold(self, mock_fetch):
        mock_fetch.return_value = {
            "skills": [{"name": "test", "installs": 500, "source": "owner/repo", "skillId": "test"}],
        }
        assert _check_installs("owner/repo@test") is False

    @patch("meta_agent.tools.skills_tools._fetch_search_api")
    def test_not_found(self, mock_fetch):
        mock_fetch.return_value = {"skills": []}
        assert _check_installs("owner/repo@nonexistent") is False


class TestInstallSkill:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("meta_agent.tools.skills_tools._check_installs")
    def test_rejects_below_threshold(self, mock_check):
        mock_check.return_value = False
        result = install_skill("owner/repo@low-skill", "test-agent")
        assert result["status"] == "error"
        assert "1,000" in result["message"]

    @patch("meta_agent.tools.skills_tools._download_skill")
    @patch("meta_agent.tools.skills_tools._check_installs")
    def test_install_success(self, mock_check, mock_download):
        mock_check.return_value = True
        # Create a fake downloaded skill
        skill_dir = os.path.join(self.tmpdir, "test-skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\ndescription: Test.\n---\nInstructions.\n")
        mock_download.return_value = skill_dir

        # Create agent dir with skills/
        agent_dir = os.path.join(self.tmpdir, "test-agent", "test_agent", "skills")
        os.makedirs(agent_dir)

        mock_ctx = MagicMock()
        mock_ctx.state = {"agent_output_dir": self.tmpdir, "current_agent_name": "test-agent", "current_agent_package": "test_agent"}

        result = install_skill("owner/repo@test-skill", "test-agent", tool_context=mock_ctx)
        assert result["status"] == "ok"
        assert os.path.isfile(os.path.join(agent_dir, "test-skill", "SKILL.md"))


class TestReadSkillContext:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("meta_agent.tools.skills_tools._check_installs")
    def test_rejects_below_threshold(self, mock_check):
        mock_check.return_value = False
        result = read_skill_context("owner/repo@low-skill")
        assert result["status"] == "error"
        assert "1,000" in result["message"]

    @patch("meta_agent.tools.skills_tools._download_skill")
    @patch("meta_agent.tools.skills_tools._check_installs")
    def test_returns_content(self, mock_check, mock_download):
        mock_check.return_value = True
        # Create fake skill
        skill_dir = os.path.join(self.tmpdir, "read-skill")
        os.makedirs(os.path.join(skill_dir, "references"))
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("---\nname: read-skill\ndescription: Test.\n---\nInstructions here.\n")
        with open(os.path.join(skill_dir, "references", "guide.md"), "w") as f:
            f.write("# Guide\nReference content.")
        mock_download.return_value = skill_dir

        result = read_skill_context("owner/repo@read-skill")
        assert result["status"] == "ok"
        assert "Instructions here." in result["skill_md"]
        assert "guide.md" in result["references"]
        assert "Reference content." in result["references"]["guide.md"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_install_skill.py -v
```

Expected: FAIL — `_download_skill`, `_check_installs` not found

- [ ] **Step 3: Implement install_skill and read_skill_context**

Add to `meta_agent/tools/skills_tools.py` (after search_skills code):

```python
# ── Install count check ──────────────────────────────────────────────


def _check_installs(package: str) -> bool:
    """Check if a skill package has >= MIN_INSTALLS.

    Args:
        package: Package identifier like "owner/repo@skill-name"
    """
    # Extract skill name from package identifier
    if "@" in package:
        skill_name = package.split("@", 1)[1]
    else:
        skill_name = package.rsplit("/", 1)[-1]

    try:
        data = _fetch_search_api(skill_name)
    except Exception:
        return False

    for s in data.get("skills", []):
        pkg_id = f"{s.get('source', '')}@{s.get('skillId', '')}"
        if pkg_id == package or s.get("name") == skill_name:
            return s.get("installs", 0) >= MIN_INSTALLS

    return False


# ── Download via CLI ─────────────────────────────────────────────────


def _download_skill(package: str) -> str:
    """Download a skill package via npx skills CLI to a temp directory.

    Args:
        package: Package identifier like "owner/repo@skill-name"

    Returns:
        Path to the downloaded skill directory.

    Raises:
        RuntimeError: If download fails.
    """
    tmpdir = tempfile.mkdtemp(prefix="skills_")

    # Parse package: "owner/repo@skill-name" -> repo="owner/repo", skill="skill-name"
    if "@" in package:
        repo, skill_name = package.rsplit("@", 1)
    else:
        repo = package
        skill_name = None

    # Build command
    cmd = ["npx", "skills", "add", repo, "-y", "--copy"]
    if skill_name:
        cmd.extend(["-s", skill_name])

    try:
        result = subprocess.run(
            cmd,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"Download timed out for {package}")
    except FileNotFoundError:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("npx not found — Node.js is required for skill downloads")

    if result.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"Download failed for {package}: {result.stderr[:500]}")

    # Find the downloaded skill directory — look for SKILL.md
    for dirpath, dirnames, filenames in os.walk(tmpdir):
        for fname in filenames:
            if fname.lower() == "skill.md":
                return dirpath

    shutil.rmtree(tmpdir, ignore_errors=True)
    raise RuntimeError(f"No SKILL.md found after downloading {package}")


# ── install_skill ────────────────────────────────────────────────────


def install_skill(package: str, agent_name: str, tool_context=None) -> dict:
    """Download a community skill, adapt it for ADK, and install into a generated agent.

    Downloads from skills.sh, strips non-ADK frontmatter, fixes naming,
    validates with load_skill_from_dir, and copies to the agent's skills/ directory.

    Only skills with 1,000+ installs are allowed (security threshold).

    Args:
        package: Package identifier from search_skills (e.g., "microsoft/azure-skills@azure-kubernetes")
        agent_name: Name of the generated agent to install into

    Returns:
        Success/failure with adaptation warnings and installed path.
    """
    # Security check
    if not _check_installs(package):
        return {
            "status": "error",
            "message": f"Skill '{package}' has fewer than 1,000 installs. Only skills with 1,000+ installs are allowed for security.",
        }

    # Download
    try:
        skill_dir = _download_skill(package)
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}

    # Adapt for ADK
    try:
        adapted_dir, warnings = adapt_skill_for_adk(skill_dir)
    except Exception as e:
        return {"status": "error", "message": f"Adaptation failed: {e}"}

    # Validate with ADK
    try:
        from google.adk.skills import load_skill_from_dir
        load_skill_from_dir(adapted_dir)
    except Exception as e:
        return {"status": "error", "message": f"ADK validation failed after adaptation: {e}"}

    # Copy to agent's skills/ directory
    output_dir = _OUTPUT_DIR
    if tool_context is not None:
        output_dir = tool_context.state.get("agent_output_dir", _OUTPUT_DIR)
        agent_pkg = tool_context.state.get("current_agent_package", agent_name.replace("-", "_"))
    else:
        agent_pkg = agent_name.replace("-", "_")

    skill_name = os.path.basename(adapted_dir)
    dest = os.path.join(output_dir, agent_name, agent_pkg, "skills", skill_name)

    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(adapted_dir, dest)

    # Cleanup temp
    tmp_root = adapted_dir
    while os.path.basename(os.path.dirname(tmp_root)) != "":
        parent = os.path.dirname(tmp_root)
        if parent.startswith(tempfile.gettempdir()):
            tmp_root = parent
            break
        tmp_root = parent
    if tmp_root.startswith(tempfile.gettempdir()) and tmp_root != tempfile.gettempdir():
        shutil.rmtree(tmp_root, ignore_errors=True)

    return {
        "status": "ok",
        "message": f"Installed skill '{skill_name}' into {agent_name}",
        "skill_name": skill_name,
        "path": dest,
        "warnings": warnings,
    }


install_skill_tool = FunctionTool(func=install_skill)


# ── read_skill_context ───────────────────────────────────────────────


def read_skill_context(package: str, tool_context=None) -> dict:
    """Download a community skill and return its content as context.

    Use this to read a skill's instructions and references for inspiration
    when writing custom ADK skills. Does NOT install the skill.

    Only skills with 1,000+ installs are allowed (security threshold).

    Args:
        package: Package identifier from search_skills (e.g., "microsoft/azure-skills@azure-kubernetes")

    Returns:
        The SKILL.md content and any reference files as strings.
    """
    # Security check
    if not _check_installs(package):
        return {
            "status": "error",
            "message": f"Skill '{package}' has fewer than 1,000 installs. Only skills with 1,000+ installs are allowed for security.",
        }

    # Download
    try:
        skill_dir = _download_skill(package)
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}

    # Read SKILL.md
    skill_md_content = ""
    for fname in os.listdir(skill_dir):
        if fname.lower() == "skill.md":
            skill_md_content = Path(os.path.join(skill_dir, fname)).read_text(encoding="utf-8")
            break

    # Read references
    references = {}
    refs_dir = os.path.join(skill_dir, "references")
    if os.path.isdir(refs_dir):
        for fname in sorted(os.listdir(refs_dir)):
            fpath = os.path.join(refs_dir, fname)
            if os.path.isfile(fpath) and fname.endswith(".md"):
                try:
                    references[fname] = Path(fpath).read_text(encoding="utf-8")
                except Exception:
                    pass

    # Cleanup temp
    tmp_root = skill_dir
    while os.path.basename(os.path.dirname(tmp_root)) != "":
        parent = os.path.dirname(tmp_root)
        if parent.startswith(tempfile.gettempdir()):
            tmp_root = parent
            break
        tmp_root = parent
    if tmp_root.startswith(tempfile.gettempdir()) and tmp_root != tempfile.gettempdir():
        shutil.rmtree(tmp_root, ignore_errors=True)

    return {
        "status": "ok",
        "message": f"Read skill content from '{package}'",
        "skill_md": skill_md_content,
        "references": references,
        "package": package,
    }


read_skill_context_tool = FunctionTool(func=read_skill_context)
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_install_skill.py -v
```

Expected: All PASS

- [ ] **Step 5: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All existing 47 + new tests PASS

- [ ] **Step 6: Commit**

```bash
git add meta_agent/tools/skills_tools.py tests/test_install_skill.py
git commit -m "feat: add install_skill and read_skill_context tools

install_skill: download + adapt + validate + copy to agent skills/.
read_skill_context: download + return content as LLM context.
Both enforce 1K+ installs security threshold."
```

---

### Task 4: Wire Tools + Update System Prompt

Register the three new tools and tell the meta-agent about them.

**Files:**
- Modify: `meta_agent/tools/__init__.py:1-16`
- Modify: `meta_agent/prompt/instructions.py:22-26` and `:57-67`

- [ ] **Step 1: Update `meta_agent/tools/__init__.py`**

Replace the full content with:

```python
"""Meta-agent tools — file ops, scaffolding, validation, and skill discovery."""

from .file_tools import write_file_tool, read_file_tool, list_files_tool
from .scaffold_tool import scaffold_agent_tool
from .validate_tool import validate_agent_tool
from .skills_tools import search_skills_tool, install_skill_tool, read_skill_context_tool


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
    ]
```

- [ ] **Step 2: Update `meta_agent/prompt/instructions.py`**

Add skill discovery tools to the capabilities section (line ~24). Change:

```python
1. **Function Tools** for file operations: scaffold_agent, write_file, read_file, list_files, validate_agent
```

To:

```python
1. **Function Tools** for file operations and skill discovery: scaffold_agent, write_file, read_file, list_files, validate_agent, search_skills, install_skill, read_skill_context
```

Add section 4b after the existing section 4 (Generate). Insert between the `write_file` list and section 5. Add this block:

```
## 4b. Discover Existing Skills (optional)
Before writing skills from scratch, search for community skills on skills.sh:
- Call `search_skills("keyword")` to find relevant community skills (only shows skills with 1K+ installs)
- Call `read_skill_context("owner/repo@skill-name")` to read a skill's content as inspiration for writing a better custom version
- Call `install_skill("owner/repo@skill-name", agent_name)` to install a skill directly (auto-adapted for ADK compatibility)

**Strategy:** Prefer installing proven community skills over writing from scratch when a good match exists. When no exact match exists, use community skills as context to write better custom skills.

Installed skills are automatically adapted for ADK: non-standard frontmatter is stripped, names are normalized to kebab-case, and the skill is validated with `load_skill_from_dir` before installation.
```

- [ ] **Step 3: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: All PASS (no test changes, just wiring)

- [ ] **Step 4: Commit**

```bash
git add meta_agent/tools/__init__.py meta_agent/prompt/instructions.py
git commit -m "feat: wire skill discovery tools into meta-agent

Adds search_skills, install_skill, read_skill_context to get_tools().
Updates system prompt with skill discovery workflow (section 4b)."
```

- [ ] **Step 5: Push**

```bash
git push
```

---

## Implementation Notes

### Parallelizable Tasks
- **Task 1** must complete first (adaptation pipeline is the foundation)
- **Tasks 2 and 3** can run in parallel (search is independent from install/read)
- **Task 4** depends on Tasks 1-3 (wires everything together)

### Testing Strategy
- Task 1: Pure function tests (no mocking, no network)
- Task 2: Mocked HTTP responses (no real API calls in tests)
- Task 3: Mocked CLI + mock check_installs (no real downloads in tests)
- Task 4: No new tests — existing tests cover the tools, this just wires them
