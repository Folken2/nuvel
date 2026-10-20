"""Pins skill hot-reload in generated ADK agents.

``LazySkillToolset`` in ``{{agent_package}}/agent.py.tmpl`` is what makes a
self-authored skill (``author_skill``, available with ``--persona``) queryable on
the next invocation without a process restart. It exists because google-adk's
``SkillToolset`` freezes its catalog at construction.

Two things are pinned here:

1. Our wrapper really does pick up an added / edited / removed skill directory on
   the next ``get_tools()`` call. If a refactor breaks that, self-authored skills
   silently stop loading — this fails instead.
2. Plain ``SkillToolset`` still does *not* rescan the directory, and the native
   revalidation path is still off by default behind an experimental flag. If a
   future ADK bump changes either, those tests fail and the wrapper should be
   re-evaluated for retirement rather than left as dead weight.

The template is text, not an importable module, so the class is extracted with
``ast`` and executed against a stub ``skills_dir()``.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import Optional

import pytest
from google.adk.features._feature_registry import FeatureName, FeatureStage
from google.adk.features._feature_registry import (
    _FEATURE_REGISTRY as ADK_FEATURE_REGISTRY,
)
from google.adk.skills import load_skill_from_dir, load_skills_from_dir
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.skill_toolset import SkillLifecycleConfig, SkillToolset

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "nuvel"
    / "backends"
    / "adk"
    / "templates"
    / "{{agent_package}}"
    / "agent.py.tmpl"
)


def _load_lazy_skill_toolset(skills_root: Path):
    """Returns the template's ``LazySkillToolset`` bound to ``skills_root``."""
    tree = ast.parse(TEMPLATE.read_text(encoding="utf-8"))
    node = next(
        (
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "LazySkillToolset"
        ),
        None,
    )
    assert node is not None, "LazySkillToolset missing from agent.py.tmpl"

    class _Logger:
        def warning(self, *a, **k):
            pass

        def info(self, *a, **k):
            pass

    namespace = {
        "BaseToolset": BaseToolset,
        "SkillToolset": SkillToolset,
        "load_skill_from_dir": load_skill_from_dir,
        "Optional": Optional,
        "logger": _Logger(),
        "skills_dir": lambda: skills_root,
    }
    exec(ast.unparse(node), namespace)  # noqa: S102 - template code under test
    return namespace["LazySkillToolset"]


def _write_skill(root: Path, name: str, description: str, body: str = "Body.") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    _write_skill(root, "alpha", "Alpha skill.")
    _write_skill(root, "beta", "Beta skill.")
    return root


def _catalog(toolset) -> list[str]:
    inner = toolset._inner
    return sorted(s.name for s in inner._list_skills()) if inner else []


async def _names(toolset) -> list[str]:
    return sorted(t.name for t in await toolset.get_tools(None))


# --------------------------------------------------------------------------
# What LazySkillToolset guarantees
# --------------------------------------------------------------------------


async def test_lazy_toolset_exposes_initial_catalog(skills_root):
    toolset = _load_lazy_skill_toolset(skills_root)()
    assert "list_skills" in await _names(toolset)
    assert _catalog(toolset) == ["alpha", "beta"]


async def test_added_skill_dir_is_queryable_on_next_get_tools(skills_root):
    """The self-authoring path: author_skill writes a dir, next turn sees it."""
    toolset = _load_lazy_skill_toolset(skills_root)()
    await toolset.get_tools(None)
    assert _catalog(toolset) == ["alpha", "beta"]

    _write_skill(skills_root, "gamma", "Authored at runtime.")

    await toolset.get_tools(None)
    assert _catalog(toolset) == ["alpha", "beta", "gamma"]


async def test_edited_skill_md_is_reflected_on_next_get_tools(skills_root):
    toolset = _load_lazy_skill_toolset(skills_root)()
    await toolset.get_tools(None)

    _write_skill(skills_root, "alpha", "Alpha skill, revised.")

    await toolset.get_tools(None)
    descriptions = {
        s.name: s.frontmatter.description for s in toolset._inner._list_skills()
    }
    assert descriptions["alpha"] == "Alpha skill, revised."


async def test_removed_skill_dir_drops_out_on_next_get_tools(skills_root):
    toolset = _load_lazy_skill_toolset(skills_root)()
    await toolset.get_tools(None)
    assert _catalog(toolset) == ["alpha", "beta"]

    shutil.rmtree(skills_root / "beta")

    await toolset.get_tools(None)
    assert _catalog(toolset) == ["alpha"]


async def test_empty_skills_dir_yields_no_tools(tmp_path):
    toolset = _load_lazy_skill_toolset(tmp_path / "nonexistent")()
    assert await toolset.get_tools(None) == []


async def test_resource_only_edit_is_not_detected(skills_root):
    """Documents the accepted gap: the scan only watches SKILL.md mtimes.

    Editing ``references/`` alone does not bump any SKILL.md mtime nor the
    top-level dir mtime, so no rebuild happens. If this ever starts failing the
    scan got broader, and the caveat in the template docstring should be dropped.
    """
    refs = skills_root / "alpha" / "references"
    refs.mkdir(parents=True)
    (refs / "notes.md").write_text("original", encoding="utf-8")

    toolset = _load_lazy_skill_toolset(skills_root)()
    await toolset.get_tools(None)
    before = toolset._inner

    (refs / "notes.md").write_text("rewritten", encoding="utf-8")

    await toolset.get_tools(None)
    assert toolset._inner is before, "unexpected rebuild on resource-only edit"


# --------------------------------------------------------------------------
# Why it is not redundant with google-adk's native behavior
# --------------------------------------------------------------------------


async def test_plain_skill_toolset_does_not_rescan_the_directory(skills_root):
    """Tripwire. If ADK gains folder rescan, revisit retiring the wrapper."""
    toolset = SkillToolset(skills=load_skills_from_dir(skills_root))
    assert sorted(s.name for s in toolset._list_skills()) == ["alpha", "beta"]

    _write_skill(skills_root, "gamma", "Authored at runtime.")
    shutil.rmtree(skills_root / "beta")
    await toolset.get_tools(None)

    assert sorted(s.name for s in toolset._list_skills()) == ["alpha", "beta"]


def test_native_revalidation_is_off_by_default():
    assert SkillLifecycleConfig().revalidate_skills is False
    assert SkillToolset(skills=[])._revalidate_skills is False


async def test_native_revalidation_cannot_see_disk_without_a_registry(skills_root):
    """``_get_or_fetch_skill`` returns the in-memory copy first.

    So even with ``revalidate_skills=True``, a registry-less toolset compares its
    own frozen skill against the hash of that same object.
    """
    toolset = SkillToolset(
        skills=load_skills_from_dir(skills_root),
        lifecycle_config=SkillLifecycleConfig(revalidate_skills=True),
    )
    assert toolset._revalidate_skills is True
    assert toolset._registry is None

    _write_skill(skills_root, "alpha", "Alpha skill, revised on disk.")

    fetched = await toolset._get_or_fetch_skill("alpha")
    assert fetched is not None
    assert fetched.frontmatter.description == "Alpha skill."


def test_skill_lifecycle_is_still_experimental_and_default_off():
    """Generated agents must not need ADK_ENABLE_SKILL_LIFECYCLE=1.

    If this fails, ADK promoted the lifecycle feature; re-read
    ``_restate_changed_skills`` before deciding the wrapper can go.
    """
    config = ADK_FEATURE_REGISTRY[FeatureName.SKILL_LIFECYCLE]
    assert config.stage is FeatureStage.EXPERIMENTAL
    assert config.default_on is False


def test_template_documents_why_the_wrapper_stays():
    """Keeps the evidence next to the code so it is not re-litigated."""
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "class LazySkillToolset" in source
    for marker in (
        "google-adk 2.10.0",
        "revalidate_skills",
        "ADK_ENABLE_SKILL_LIFECYCLE",
        "test_skill_hot_reload.py",
    ):
        assert marker in source, f"missing rationale marker: {marker}"

