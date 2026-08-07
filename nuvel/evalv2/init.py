"""nuvel evalv2 — eval suite bootstrapping.

``init_eval_suite`` stamps a starter ``eval/`` directory into an existing
skill, giving a developer the v2 contract to fill in:

    eval/
    ├── suite.yaml        # default template (name, evaluators, thresholds)
    └── examples/         # empty, kept via .gitkeep

The default suite pairs an LLM rubric judge with a deterministic length
check — the two evaluator kinds the runner understands out of the box. It's a
starting point: edit the rubric, add examples, tune the thresholds.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["init_eval_suite", "default_suite_yaml"]


def default_suite_yaml(skill: str, description: str = "") -> str:
    """Render the default v2 ``suite.yaml`` for a skill.

    ``skill`` names the suite (``<skill>-eval``) and fills the ``skill`` field.
    ``description`` overrides the default one-line description when provided.
    """
    desc = description or f"Initial eval suite for the {skill} skill"
    return (
        f"name: {skill}-eval\n"
        f"description: {desc}\n"
        f"skill: {skill}\n"
        "evaluators:\n"
        "  - llm-judge:\n"
        "      rubric:\n"
        "        accuracy: 0.5\n"
        "        helpfulness: 0.3\n"
        "        tone: 0.2\n"
        "      model: openai/gpt-4o-mini\n"
        "      max_cost: 0.10\n"
        "  - deterministic:\n"
        "      - { type: max-length, max_chars: 3000 }\n"
        "thresholds:\n"
        "  pass: 0.8\n"
        "  warn: 0.6\n"
        "  regression: -0.05\n"
    )


def init_eval_suite(
    skill_dir: Path,
    name: str | None = None,
    description: str = "",
    force: bool = False,
) -> Path:
    """Initialize an ``eval/`` directory in a skill.

    Creates:
      ``<skill_dir>/eval/suite.yaml``     (default v2 suite template)
      ``<skill_dir>/eval/examples/``      (empty dir with .gitkeep)

    ``name`` defaults to ``<skill_dir>.name-eval``. ``description`` overrides
    the template's default one-liner.

    Raises ``FileNotFoundError`` if ``skill_dir`` does not exist, and
    ``FileExistsError`` if ``eval/`` already exists and ``force`` is False.
    Returns the ``eval/`` directory path.
    """
    skill_dir = Path(skill_dir)
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory does not exist: {skill_dir}")

    suite_name = name or skill_dir.name
    eval_dir = skill_dir / "eval"

    if eval_dir.exists() and not force:
        raise FileExistsError(
            f"eval/ already exists in {skill_dir}; pass force=True to overwrite."
        )

    examples_dir = eval_dir / "examples"
    examples_dir.mkdir(parents=True, exist_ok=True)

    (eval_dir / "suite.yaml").write_text(
        default_suite_yaml(suite_name, description), encoding="utf-8"
    )
    (examples_dir / ".gitkeep").write_text("", encoding="utf-8")

    return eval_dir
