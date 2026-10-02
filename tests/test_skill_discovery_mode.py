"""Pins google-adk 2.10's skill-disclosure surface, and why nuvel leaves it alone.

Wave 3 evaluated ADK 2.10's ``SkillDiscoveryMode`` and the ``max_active_skills``
cap for nuvel's 15 ADK knowledge skills. The outcome was *don't adopt*, for two
measured reasons that these tests pin so a future ADK bump trips on them:

1. ``SkillDiscoveryMode.LAZY`` is already the constructor default
   (``google/adk/tools/skill_toolset.py``:1730) and is already the on-demand
   disclosure nuvel wants: the catalog stays out of the system instruction and
   reaches the model only when it calls ``list_skills``. The alternative,
   ``EAGER``, injects the catalog into every request — measured at roughly 7k
   extra characters (~1.8k tokens) per request for our catalog. Switching modes
   would *add* resident context, not remove it. If ADK ever flips the default,
   ``test_lazy_is_the_adk_default`` fails.
2. The active-skill cap only reclaims context when the experimental
   ``ADK_ENABLE_SKILL_LIFECYCLE`` flag is on, because the transcript rewrite
   that drops a released skill's instructions
   (``SkillToolset._prune_unloaded_skills``:2374-2377) is gated on it. With the
   flag off the cap still evicts skills — capability lost, nothing reclaimed.
   A generated agent must not depend on an experimental flag, so the cap stays
   out.

Also pinned here: ``LazySkillToolset`` in ``{{agent_package}}/agent.py.tmpl``
forwards ``process_llm_request``. The flow calls that on whatever sits in
``agent.tools`` — the wrapper — and ``BaseToolset``'s implementation is a no-op,
so without the override a generated agent gets the skill tools but none of the
skill guidance.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Optional

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.features._feature_registry import (
    _FEATURE_REGISTRY as ADK_FEATURE_REGISTRY,
)
from google.adk.features._feature_registry import FeatureName, FeatureStage
from google.adk.models.llm_request import LlmRequest
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.skills import load_skill_from_dir
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.skill_toolset import (
    SkillDiscoveryMode,
    SkillLifecycleConfig,
    SkillLifecycleMode,
    SkillToolset,
)
from google.adk.tools.tool_context import ToolContext
from google.genai import types

REPO = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO / "nuvel" / "backends" / "adk" / "skills"
TEMPLATE = (
    REPO
    / "nuvel"
    / "backends"
    / "adk"
    / "templates"
    / "{{agent_package}}"
    / "agent.py.tmpl"
)


@pytest.fixture(scope="module")
def adk_skills():
    """The real ADK knowledge skills the meta-agent serves."""
    skills = [
        load_skill_from_dir(d)
        for d in sorted(SKILLS_DIR.iterdir())
        if d.is_dir() and (d / "SKILL.md").exists()
    ]
    assert len(skills) >= 15, f"expected the full ADK skill set, got {len(skills)}"
    return skills


async def _contexts(toolset):
    """A ToolContext / ReadonlyContext pair for an agent holding ``toolset``."""
    agent = LlmAgent(model="gemini-2.0-flash", name="probe", tools=[toolset])
    service = InMemorySessionService()
    session = await service.create_session(app_name="probe", user_id="u")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="probe-inv-1",
        agent=agent,
        session=session,
    )
    return ToolContext(ctx), ReadonlyContext(ctx)


async def _system_instruction(toolset):
    tool_ctx, _ = await _contexts(toolset)
    request = LlmRequest()
    await toolset.process_llm_request(tool_context=tool_ctx, llm_request=request)
    return request.config.system_instruction or ""


# --------------------------------------------------------------------------
# 1. The disclosure surface itself
# --------------------------------------------------------------------------


def test_discovery_modes_are_exactly_lazy_and_eager():
    """A third mode would be a new option worth re-evaluating."""
    assert {m.name for m in SkillDiscoveryMode} == {"LAZY", "EAGER"}
    assert SkillDiscoveryMode.LAZY.value == "lazy"
    assert SkillDiscoveryMode.EAGER.value == "eager"


def test_lazy_is_the_adk_default():
    """nuvel relies on the default being the on-demand mode.

    Neither ``nuvel/agent.py`` nor the generated-agent template passes
    ``discovery_mode``. If ADK flips this to EAGER, every request grows by the
    whole catalog and this fails.
    """
    import inspect

    default = inspect.signature(SkillToolset.__init__).parameters[
        "discovery_mode"
    ].default
    assert default is SkillDiscoveryMode.LAZY


def test_discovery_mode_is_not_feature_gated():
    """``discovery_mode`` rides on the stable SkillToolset surface.

    Recorded for contrast with the lifecycle surface below: the mode is usable
    without an experimental flag, so "experimental" is not the reason nuvel
    declines it — the measured cost is.
    """
    assert (
        ADK_FEATURE_REGISTRY[FeatureName.SKILL_TOOLSET].stage is FeatureStage.STABLE
    )
    assert ADK_FEATURE_REGISTRY[FeatureName.SKILL_TOOLSET].default_on is True


async def test_lazy_keeps_the_catalog_out_of_the_system_instruction(adk_skills):
    """LAZY offers ``list_skills``; EAGER inlines the catalog instead."""
    lazy = SkillToolset(skills=adk_skills, discovery_mode=SkillDiscoveryMode.LAZY)
    eager = SkillToolset(skills=adk_skills, discovery_mode=SkillDiscoveryMode.EAGER)

    _, lazy_ro = await _contexts(lazy)
    _, eager_ro = await _contexts(eager)
    lazy_tools = {t.name for t in await lazy.get_tools(lazy_ro)}
    eager_tools = {t.name for t in await eager.get_tools(eager_ro)}

    assert "list_skills" in lazy_tools
    assert "list_skills" not in eager_tools
    assert {"load_skill", "load_skill_resource"} <= lazy_tools & eager_tools

    lazy_instruction = await _system_instruction(lazy)
    eager_instruction = await _system_instruction(eager)
    assert "<available_skills>" not in lazy_instruction
    assert "<available_skills>" in eager_instruction
    for skill in adk_skills:
        assert skill.name not in lazy_instruction
        assert skill.name in eager_instruction


async def test_eager_costs_materially_more_resident_context(adk_skills):
    """The measurement behind the don't-adopt call.

    Wave 3 measured EAGER at ~+7.1k chars (~+1.8k tokens) of per-request system
    instruction over LAZY for this catalog. The threshold is deliberately loose
    — it is there to catch the trade reversing, not to pin an exact byte count.
    """
    lazy = SkillToolset(skills=adk_skills, discovery_mode=SkillDiscoveryMode.LAZY)
    eager = SkillToolset(skills=adk_skills, discovery_mode=SkillDiscoveryMode.EAGER)

    lazy_chars = len(await _system_instruction(lazy))
    eager_chars = len(await _system_instruction(eager))

    assert eager_chars - lazy_chars > 4000, (
        "EAGER is no longer materially more expensive than LAZY for the ADK"
        f" skill set (lazy={lazy_chars}, eager={eager_chars}); re-run the Wave 3"
        " measurement before changing modes"
    )


async def test_every_skill_stays_loadable_in_both_modes(adk_skills):
    """Mode changes disclosure, not reach: ``load_skill`` works either way.

    Pins that nothing in the catalog became unreachable — the decisive check
    against a reduced-disclosure mode.
    """
    for mode in SkillDiscoveryMode:
        toolset = SkillToolset(skills=adk_skills, discovery_mode=mode)
        tool_ctx, _ = await _contexts(toolset)
        loader = next(t for t in toolset._tools if t.name == "load_skill")
        for skill in adk_skills:
            result = await loader.run_async(
                args={"skill_name": skill.name}, tool_context=tool_ctx
            )
            assert isinstance(result, dict), (mode, skill.name, result)
            assert not result.get("error"), (mode, skill.name, result)
            assert result["skill_name"] == skill.name
            assert result["instructions"], (mode, skill.name)


# --------------------------------------------------------------------------
# 2. The active-skill cap, and why it stays out
# --------------------------------------------------------------------------


def test_skill_lifecycle_is_still_experimental_and_off_by_default():
    """The cap's context reclamation rides on this flag. Tripwire for a bump."""
    config = ADK_FEATURE_REGISTRY[FeatureName.SKILL_LIFECYCLE]
    assert config.stage is FeatureStage.EXPERIMENTAL
    assert config.default_on is False


def test_cap_default_never_evicts(adk_skills):
    """Out of the box the cap is inert, so nuvel inherits no behaviour change.

    ``max_active_skills`` defaults to 5 but only BOUNDED skills count against
    it, and ``default_mode`` is PERSISTENT.
    """
    config = SkillLifecycleConfig()
    assert config.max_active_skills == 5
    assert config.default_mode is SkillLifecycleMode.PERSISTENT

    toolset = SkillToolset(skills=adk_skills)
    assert toolset._evict_over_cap([s.name for s in adk_skills[:8]]) == []


async def test_cap_without_the_flag_loses_capability_and_reclaims_nothing(
    adk_skills, monkeypatch
):
    """The reason the cap is not worth adopting unflagged.

    With BOUNDED skills and the flag off, loading past the cap drops skills from
    the active set — their tools go away — while the instructions they put in
    the transcript stay there. Pay the context, lose the capability.
    """
    monkeypatch.delenv("ADK_ENABLE_SKILL_LIFECYCLE", raising=False)
    toolset = SkillToolset(
        skills=adk_skills,
        lifecycle_config=SkillLifecycleConfig(
            default_mode=SkillLifecycleMode.BOUNDED, max_active_skills=5
        ),
    )
    assert toolset._lifecycle_enabled is False

    tool_ctx, _ = await _contexts(toolset)
    loader = next(t for t in toolset._tools if t.name == "load_skill")
    history = []
    for skill in adk_skills[:8]:
        response = await loader.run_async(
            args={"skill_name": skill.name}, tool_context=tool_ctx
        )
        history.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="load_skill", response=response
                        )
                    )
                ],
            )
        )

    # Capability lost: 8 loaded, 5 active.
    assert len(toolset.list_active_skills(tool_ctx)) == 5

    def transcript_size(contents):
        return sum(
            len(json.dumps(part.function_response.response))
            for content in contents
            for part in (content.parts or [])
            if part.function_response
        )

    before = transcript_size(history)
    request = LlmRequest(contents=list(history))
    await toolset.process_llm_request(
        tool_context=tool_ctx, llm_request=request
    )
    assert transcript_size(request.contents) == before, (
        "ADK now prunes released skills' instructions without"
        " ADK_ENABLE_SKILL_LIFECYCLE; re-evaluate adopting the active-skill cap"
    )


# --------------------------------------------------------------------------
# 3. The generated-agent wrapper
# --------------------------------------------------------------------------


def _load_lazy_skill_toolset(skills_root: Path):
    """Returns the template's ``LazySkillToolset`` bound to ``skills_root``.

    The template is text, not an importable module, so the class is extracted
    with ``ast`` and executed against a stub ``skills_dir()``.
    """
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


def test_wrapper_overrides_process_llm_request():
    """A wrapper that inherits the no-op silently drops all skill guidance."""
    lazy_cls = _load_lazy_skill_toolset(SKILLS_DIR)
    assert (
        lazy_cls.process_llm_request is not BaseToolset.process_llm_request
    ), (
        "LazySkillToolset must forward process_llm_request; BaseToolset's is a"
        " no-op and the flow calls it on the wrapper, not the inner toolset"
    )


async def test_wrapper_injects_the_same_guidance_as_the_inner_toolset(adk_skills):
    """What the model actually receives through the wrapper."""
    lazy_cls = _load_lazy_skill_toolset(SKILLS_DIR)
    wrapper = lazy_cls()

    wrapper_instruction = await _system_instruction(wrapper)
    assert wrapper_instruction, "wrapper injected no skill guidance"
    assert "load_skill" in wrapper_instruction
    # LAZY disclosure survives the wrapper: names are not in the prompt.
    assert "<available_skills>" not in wrapper_instruction

    inner_instruction = await _system_instruction(wrapper._inner)
    assert wrapper_instruction == inner_instruction


async def test_rebuild_resets_the_inner_toolset_to_default_config(adk_skills):
    """``_rebuild_if_needed`` builds a fresh default-configured SkillToolset.

    Documented, not desired: today nuvel wants the defaults, so there is nothing
    to carry over. Any future non-default constructor argument has to be set
    inside ``_rebuild_if_needed`` or it is lost on the next skill edit.
    """
    lazy_cls = _load_lazy_skill_toolset(SKILLS_DIR)
    wrapper = lazy_cls()
    _, readonly = await _contexts(wrapper)

    await wrapper.get_tools(readonly)
    first = wrapper._inner
    assert first._discovery_mode is SkillDiscoveryMode.LAZY

    first._discovery_mode = SkillDiscoveryMode.EAGER
    wrapper._mtime = -1.0  # force the mtime-change rebuild path
    await wrapper.get_tools(readonly)

    assert wrapper._inner is not first
    assert wrapper._inner._discovery_mode is SkillDiscoveryMode.LAZY
