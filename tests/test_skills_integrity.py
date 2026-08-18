"""Integrity tests for the bundled knowledge skills.

These exist because four PRs between v0.2.0 and 643994f shipped subsystems with no
skill coverage. They turn "remember to document the skill" into a failing build.

Three guards, all static checks over `nuvel/backends/*/skills/`:

1. `test_referenced_files_exist` — a SKILL.md must not cite a `references/*.md`
   that isn't on disk.
2. `test_frontmatter_is_valid` — valid YAML frontmatter, `name` matching the
   directory, non-empty `description`.
3. `test_skill_count_matches_expectation` — per-framework skill counts match
   `EXPECTED_SKILL_COUNTS`, so adding a skill forces an explicit update here.
4. `test_every_package_directory_is_named_by_a_skill` — every non-exempt package
   directory under templates/{{agent_package}}/ is named by at least one skill, so a
   new subsystem can't ship undocumented.

Scope note: this file asserts nothing about the template env surface — there is no
`.env.example` parity check here yet.
"""

from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKENDS = REPO_ROOT / "nuvel" / "backends"

FRAMEWORK_DIRS = {
    "adk": BACKENDS / "adk" / "skills",
    "claude_agent_sdk": BACKENDS / "claude_agent_sdk" / "skills",
    "anthropic_managed_agents": BACKENDS / "anthropic_managed_agents" / "skills",
}

EXPECTED_SKILL_COUNTS = {"adk": 15, "claude_agent_sdk": 6, "anthropic_managed_agents": 5}

REFERENCE_RE = re.compile(r"references/([a-z0-9][a-z0-9-]*\.md)")


def _skill_dirs(framework: str) -> list[Path]:
    root = FRAMEWORK_DIRS[framework]
    return sorted(p for p in root.iterdir() if (p / "SKILL.md").is_file())


def _all_skill_dirs() -> list[Path]:
    out: list[Path] = []
    for framework in FRAMEWORK_DIRS:
        out.extend(_skill_dirs(framework))
    return out


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{skill_md} has no YAML frontmatter"
    _, _, rest = text.partition("---")
    front, _, _ = rest.partition("---")
    return yaml.safe_load(front) or {}


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_referenced_files_exist(skill_dir: Path) -> None:
    """A SKILL.md must not promise a reference file that isn't on disk."""
    cited = set(REFERENCE_RE.findall((skill_dir / "SKILL.md").read_text(encoding="utf-8")))
    missing = sorted(n for n in cited if not (skill_dir / "references" / n).is_file())
    assert not missing, f"{skill_dir.name} cites missing reference files: {missing}"


@pytest.mark.parametrize("skill_dir", _all_skill_dirs(), ids=lambda p: p.name)
def test_frontmatter_is_valid(skill_dir: Path) -> None:
    """Every skill needs a name matching its directory and a non-empty description."""
    meta = _frontmatter(skill_dir / "SKILL.md")
    name = meta.get("name")
    description = str(meta.get("description", "")).strip()
    assert name, f"{skill_dir.name}: frontmatter 'name' is missing or empty"
    assert description, f"{skill_dir.name}: frontmatter 'description' is missing or empty"
    assert name == skill_dir.name, (
        f"{skill_dir.name}: frontmatter name {name!r} does not match directory name"
    )


@pytest.mark.parametrize("framework", sorted(EXPECTED_SKILL_COUNTS))
def test_skill_count_matches_expectation(framework: str) -> None:
    """A new skill must be registered here, so counts in docs cannot silently drift."""
    actual = len(_skill_dirs(framework))
    expected = EXPECTED_SKILL_COUNTS[framework]
    assert actual == expected, (
        f"{framework}: found {actual} skills, expected {expected}. "
        "If this is intentional, update EXPECTED_SKILL_COUNTS and every documented "
        "count (.claude/skills/nuvel/SKILL.md, CLAUDE.md, README.md)."
    )


TEMPLATE_DIR = BACKENDS / "adk" / "templates"
ENV_EXAMPLE = TEMPLATE_DIR / ".env.example"

# Template code reads env vars five ways: os.getenv, os.environ.get/[], via a
# module-level ENV_* name constant (e.g. ENV_PRELOAD = "NUVEL_MEMORY_PRELOAD"), and via
# a local wrapper-helper idiom (e.g. _env_int("TRACE_MAX_ARGS_CHARS", 50_000) in
# trace_plugin.py) whose literal is invisible to the other four patterns because it's
# an argument to a custom function, not to os.getenv/os.environ directly.
ENV_READ_PATTERNS = (
    re.compile(r"""getenv\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""environ\.get\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    re.compile(r"""environ\[\s*["']([A-Z][A-Z0-9_]{2,})["']\s*\]"""),
    re.compile(r"""^\s*_?ENV_[A-Z0-9_]+\s*=\s*["']([A-Z][A-Z0-9_]{2,})["']""", re.M),
    re.compile(r"""\b_?(?:env|get_env)[a-z_]*\(\s*["']([A-Z][A-Z0-9_]{2,})["']"""),
    # Sixth pattern: helpers whose name *ends* in `_env` (e.g. `_int_env("FOO")`,
    # `_str_env("BAR")`). The fifth pattern only caught the `env`/`get_env` *prefix*
    # idiom; a suffix-named wrapper passing a literal straight through was invisible.
    # The helper-name group is non-capturing so findall yields only the env-var name
    # (a single string), matching every other pattern above.
    re.compile(r"""(?:\w+_env)\s*\(\s*["']([A-Z_]+)["']"""),
)

ENV_ENTRY_RE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", re.M)

# Read by template code but deliberately absent from .env.example. Each entry needs a
# reason — do not add to this set to silence a failure.
ENV_EXAMPLE_EXEMPT = {
    "RECORD": "test-only: golden-recording switch in tests/test_agent.py.tmpl",
    "HOST": "platform-provided; read only in run_adk.py's diagnostic dump",
    "TELEGRAM_BOT_TOKEN": (
        "read in two places: the --with-telegram overlay's gateway env block, and "
        "unconditionally in the base template's cron/delivery.py _send_telegram(). "
        "Still exempt because it no-ops without the rest of the overlay's machinery "
        "(gateway routes, webhook registration) — documenting it in the base "
        ".env.example would advertise a knob that does nothing on its own."
    ),
}


def _env_vars_read_by_template_code() -> set[str]:
    found: set[str] = set()
    for path in TEMPLATE_DIR.rglob("*"):
        if not path.is_file() or path.name == ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in ENV_READ_PATTERNS:
            found |= set(pattern.findall(text))
    return found


def test_every_env_var_read_by_template_is_documented() -> None:
    """A knob the template reads must appear in .env.example, or be explicitly exempt."""
    documented = set(ENV_ENTRY_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))
    undocumented = sorted(
        _env_vars_read_by_template_code() - documented - set(ENV_EXAMPLE_EXEMPT)
    )
    assert not undocumented, (
        "These env vars are read by template code but absent from .env.example: "
        f"{undocumented}. Document them, or add to ENV_EXAMPLE_EXEMPT with a reason."
    )


PLUGIN_INIT_TMPL = TEMPLATE_DIR / "{{agent_package}}" / "plugins" / "__init__.py.tmpl"
README_PATH = REPO_ROOT / "README.md"

PLUGIN_INSTANCES_SOURCE = f"{PLUGIN_INIT_TMPL} (PLUGIN_INSTANCES list, ~lines 98-116)"
README_TABLE_SOURCE = f"{README_PATH} ('## Plugin Chain' table, ~lines 256-278)"

# PLUGIN_INSTANCES holds snake_case variable names; README's Plugin Chain table uses
# class-ish display names. These do not mechanically transform into one another —
# e.g. `self_healing` is instantiated as ReflectAndRetryToolPlugin(name="self_healing",
# ...) and `save_files` as SaveFilesAsArtifactsPlugin, and `sibling_runner` is
# documented as "SiblingRunner" (no "Plugin" suffix, unlike every other row). A naive
# PascalCase+"Plugin" transform would get those three wrong, so every entry is listed
# explicitly here rather than derived. If PLUGIN_INSTANCES gains an entry not listed
# below, the test must fail loudly rather than silently skip it — a human has to decide
# the display name.
PLUGIN_INSTANCE_LABELS = {
    "memory": "MemoryPlugin",
    "cost_guard": "CostGuardPlugin",
    "context_window": "ContextWindowPlugin",
    "trace": "TracePlugin",
    "context_filter": "ContextFilterPlugin",
    "console_logger": "ConsoleLoggerPlugin",
    "tool_events": "ToolEventsPlugin",
    "resilience": "ResiliencePlugin",
    "guardrails": "GuardrailsPlugin",
    "cron_isolation": "CronIsolationPlugin",
    "cache": "CachePlugin",
    "self_healing": "ReflectAndRetryToolPlugin",
    "save_files": "SaveFilesAsArtifactsPlugin",
    "recordings": "RecordingsPlugin",
    "replay": "ReplayPlugin",
    "skill_curator": "SkillCuratorPlugin",
    "sibling_runner": "SiblingRunner",
}

# PLUGIN_INSTANCES is discovered by walking the module's AST rather than by regex.
# The old parser matched a single `[...]` list literal with the closing `]` at column 0;
# a plugin appended after the literal via `.append()`, `+=`, or inside a conditional
# block was invisible to it and got no coverage checking. The AST walker below sees the
# list literal, `PLUGIN_INSTANCES.append(x)` / `.extend([...])` calls, and
# `PLUGIN_INSTANCES += [...]` augmented assignments wherever they appear, so a plugin
# added by any of those routes is still checked against the README table.
README_PLUGIN_ROW_RE = re.compile(r"^\|\s*\*\*([A-Za-z0-9]+)\*\*\s*\|", re.M)

# A parse that finds fewer than this many entries is treated as implausible rather than
# real — this is a sanity floor to catch catastrophic truncation, not the actual plugin
# count (which must never be hand-encoded here; that's the drift this test exists to stop).
_PLUGIN_INSTANCES_MIN_PLAUSIBLE = 2


def _elt_name(node: ast.expr) -> str:
    """The bare identifier an element contributes to the plugin list."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ast.unparse(node).strip()


def _plugin_instance_names() -> list[str]:
    """Names in PLUGIN_INSTANCES, discovered by AST walk of the plugins __init__.

    Sees the initial list literal, `PLUGIN_INSTANCES.append(x)` /
    `.extend([...])` calls, and `PLUGIN_INSTANCES += [...]` extends, wherever
    they appear — so a plugin added after the literal is not invisible the way it
    was to the old single-literal regex.
    """
    raw = PLUGIN_INIT_TMPL.read_text(encoding="utf-8")
    # The template embeds `{{agent_package}}` placeholders in import statements, which
    # are not valid Python; substitute a dummy identifier so ast.parse() succeeds.
    source = re.sub(r"\{\{[^}]+\}\}", "placeholder", raw)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse {PLUGIN_INSTANCES_SOURCE} as Python for AST plugin "
            f"discovery: {exc}. If the template gained a new placeholder syntax, teach "
            "the substitution above about it."
        ) from exc

    names: list[str] = []
    for node in ast.walk(tree):
        # PLUGIN_INSTANCES = [ ... ]
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            if any(
                isinstance(t, ast.Name) and t.id == "PLUGIN_INSTANCES"
                for t in node.targets
            ):
                names.extend(_elt_name(e) for e in node.value.elts)
        # PLUGIN_INSTANCES += [ ... ]
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "PLUGIN_INSTANCES"
                and isinstance(node.value, ast.List)
            ):
                names.extend(_elt_name(e) for e in node.value.elts)
        # PLUGIN_INSTANCES.append(x) / PLUGIN_INSTANCES.extend([ ... ])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            recv = node.func.value
            if isinstance(recv, ast.Name) and recv.id == "PLUGIN_INSTANCES":
                if node.func.attr == "append":
                    names.extend(_elt_name(a) for a in node.args)
                elif node.func.attr == "extend":
                    for a in node.args:
                        if isinstance(a, ast.List):
                            names.extend(_elt_name(e) for e in a.elts)

    assert len(names) >= _PLUGIN_INSTANCES_MIN_PLAUSIBLE, (
        f"AST walk of {PLUGIN_INSTANCES_SOURCE} found only {len(names)} entrie(s): "
        f"{names}. That is implausibly few for the real plugin chain — the "
        "PLUGIN_INSTANCES assignments may have been reshaped in a way this walker "
        "doesn't understand (e.g. built from a comprehension or a helper call); "
        "treating a short parse as ground truth would silently under-check README "
        "coverage."
    )
    return names


def _readme_plugin_table_rows() -> list[str]:
    """Bolded first-column labels, one per data row, from the Plugin Chain table."""
    text = README_PATH.read_text(encoding="utf-8")
    section = re.search(r"## Plugin Chain\n(.*?)\n## ", text, re.S)
    assert section, f"could not find the '## Plugin Chain' section in {README_PATH}"
    return README_PLUGIN_ROW_RE.findall(section.group(1))


def test_plugin_chain_table_documents_every_plugin() -> None:
    """Every PLUGIN_INSTANCES entry must have a matching row in README's plugin table."""
    instances = _plugin_instance_names()

    unmapped = sorted(set(instances) - set(PLUGIN_INSTANCE_LABELS))
    assert not unmapped, (
        f"{PLUGIN_INSTANCES_SOURCE} has entries with no expected README label in "
        f"PLUGIN_INSTANCE_LABELS: {unmapped}. A snake_case variable name cannot be "
        "mechanically converted to its README display name (see the comment above "
        "PLUGIN_INSTANCE_LABELS) — add an explicit mapping entry for each name above "
        "before this test can verify it is documented."
    )

    table_rows = _readme_plugin_table_rows()
    table_labels = set(table_rows)
    missing = sorted(
        f"{name} (expected README label {PLUGIN_INSTANCE_LABELS[name]!r})"
        for name in instances
        if PLUGIN_INSTANCE_LABELS[name] not in table_labels
    )
    assert not missing, (
        f"These entries from {PLUGIN_INSTANCES_SOURCE} have no matching row in "
        f"{README_TABLE_SOURCE}: {missing}. Add a row for each, or fix its label in "
        "PLUGIN_INSTANCE_LABELS if the README wording changed."
    )

    assert len(table_rows) == len(instances), (
        f"{README_TABLE_SOURCE} has {len(table_rows)} data rows but "
        f"{PLUGIN_INSTANCES_SOURCE} has {len(instances)} entries — every plugin in the "
        "chain must have exactly one documented row (extra or duplicate rows drift "
        "just as silently as missing ones)."
    )


# ── Subsystem-level skill coverage ───────────────────────────────────────────
#
# The three guards above protect *existing* skill artifacts. They say nothing about
# the obligation to document a *new* subsystem: a fresh package directory under
# templates/{{agent_package}}/ can ship with zero skill coverage and the suite stays
# green. PRs #50/#51/#54/#55 did exactly that — guardrails, cron isolation, org memory,
# and hybrid retrieval all landed with no skill. This guard asserts that every
# non-exempt top-level package directory is *named* by at least one skill.
#
# It is a name-level check, not a count: a count failure tells you a number to change,
# a name failure tells you what to write.

AGENT_PKG_DIR = TEMPLATE_DIR / "{{agent_package}}"

# Directories that legitimately need no knowledge skill. Each entry needs a reason —
# do not add to this set merely to silence a failure; a genuinely new subsystem belongs
# in a skill, not here.
SKILL_COVERAGE_EXEMPT_DIRS = {
    "__pycache__": "build artifact, not source",
    "utils": "pure infrastructure helpers (date/resilience), no user-facing behavior",
    "state": "internal state management (session memory, query cache), not a subsystem",
    "config": "internal wiring (llm/logging/paths/seed), no user-facing knob to teach",
    "contexts": "empty scaffold directory populated per generated agent",
    "soul": "the agent's persona/identity doc (SOUL.md), not a code subsystem",
    "plugins": (
        "documented via README's '## Plugin Chain' table and guarded by "
        "test_plugin_chain_table_documents_every_plugin, not by a knowledge skill"
    ),
}


def _collect_skill_metadata() -> list[dict]:
    """Frontmatter for every bundled skill: name, description, and hermes tags."""
    out: list[dict] = []
    for skill_dir in _all_skill_dirs():
        meta = _frontmatter(skill_dir / "SKILL.md")
        hermes = (meta.get("metadata") or {}).get("hermes") or {}
        tags = hermes.get("tags") or []
        out.append(
            {
                "name": str(meta.get("name", "")),
                "description": str(meta.get("description", "")),
                "tags": [str(t) for t in tags],
            }
        )
    return out


def _package_dirs() -> list[Path]:
    """Top-level package directories under templates/{{agent_package}}/."""
    return sorted(p for p in AGENT_PKG_DIR.iterdir() if p.is_dir())


def test_every_package_directory_is_named_by_a_skill() -> None:
    """A new subsystem directory must be documented by at least one skill's frontmatter."""
    skills = _collect_skill_metadata()
    # Sanity floor: if skill discovery collapses to nothing, fail loud rather than
    # certifying blanket coverage against an empty corpus.
    assert skills, "no skills discovered — cannot verify subsystem coverage"

    searchable = [
        (s["name"], f"{s['name']} {s['description']} {' '.join(s['tags'])}".lower())
        for s in skills
    ]
    skill_names = [s["name"] for s in skills]

    failures: list[str] = []
    for pkg in _package_dirs():
        name = pkg.name
        if name in SKILL_COVERAGE_EXEMPT_DIRS:
            continue
        if any(name.lower() in haystack for _, haystack in searchable):
            continue
        closest = difflib.get_close_matches(name, skill_names, n=1, cutoff=0.0)
        hint = (
            f" Closest skill is {closest[0]!r}." if closest else " No skills to match."
        )
        failures.append(
            f"Package {name + '/'!r} is not referenced by any skill. Add a skill that "
            f"documents this subsystem, or exempt it in SKILL_COVERAGE_EXEMPT_DIRS with "
            f"a reason.{hint}"
        )

    assert not failures, "\n".join(failures)
