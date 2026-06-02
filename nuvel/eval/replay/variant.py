"""Load + discover variant YAML files.

Variants live at ``generated-agents/<agent>/evals/variants/<name>.yaml`` —
the same ``evals/`` convention as ``rubric.yaml``. Loading fails fast on
missing required fields or malformed YAML so a typo never silently degrades
to a no-op replay.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from nuvel.eval.replay.schema import Variant


def load_variant(path: Path) -> Variant:
    """Parse one variant YAML. Raises ``ValueError`` on any problem."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: YAML parse error: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: variant root must be a mapping, got {type(data).__name__}")

    for required in ("version", "name", "system_prompt"):
        if not data.get(required):
            raise ValueError(f"{path}: missing required field '{required}'")

    return Variant(
        version=str(data["version"]),
        name=str(data["name"]),
        system_prompt=str(data["system_prompt"]),
        description=str(data.get("description") or ""),
        model=data.get("model"),
        temperature=float(data.get("temperature", 0.0)),
        max_tokens=int(data.get("max_tokens", 600)),
    )


@dataclass
class DiscoveredVariant:
    """A variant plus the agent it targets and that agent's traces dir."""

    agent: str
    variant: Variant
    path: Path
    traces_dir: Path


def discover_variants(agent_filter: str | None = None) -> list[DiscoveredVariant]:
    """Scan ``generated-agents/*/evals/variants/*.yaml`` from the cwd.

    ``agent_filter`` is a case-insensitive substring match on the agent dir
    name. Malformed variant files are skipped silently here (listing must not
    crash on one bad file); ``replay`` re-loads the chosen variant and will
    surface the error then.
    """
    rows: list[DiscoveredVariant] = []
    gen = Path.cwd() / "generated-agents"
    if not gen.is_dir():
        return rows
    for agent_dir in sorted(gen.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        if agent_filter and agent_filter.lower() not in agent.lower():
            continue
        vdir = agent_dir / "evals" / "variants"
        if not vdir.is_dir():
            continue
        for yml in sorted(vdir.glob("*.yaml")):
            try:
                variant = load_variant(yml)
            except ValueError:
                continue
            rows.append(
                DiscoveredVariant(
                    agent=agent,
                    variant=variant,
                    path=yml,
                    traces_dir=agent_dir / "traces",
                )
            )
    return rows
