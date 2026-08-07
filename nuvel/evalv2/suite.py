"""EvalSuite — loads a v2 suite and discovers its examples.

A suite lives alongside a skill at ``<skill_dir>/eval/``:

    eval/
    ├── suite.yaml        # name, evaluators, thresholds
    └── examples/         # *.json / *.yaml / *.yml example files

Each example file holds either a single example object or an array of them.
Examples are collected across every file, then sorted by id so a run is
deterministic regardless of filesystem ordering.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ExampleError, SuiteError


_EXAMPLE_SUFFIXES = (".json", ".yaml", ".yml")


@dataclass
class EvalExample:
    """A single evaluation case: an input plus optional expected output."""

    id: str
    input: str
    expected_output: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "input": self.input,
            "expected_output": self.expected_output,
            "tags": list(self.tags),
        }


@dataclass
class EvalSuite:
    """A loaded suite: its config plus every discovered example."""

    name: str
    description: str = ""
    skill: str = ""
    evaluators: list[dict] = field(default_factory=list)
    thresholds: dict = field(default_factory=dict)
    examples: list[EvalExample] = field(default_factory=list)
    root: Path | None = None

    @classmethod
    def from_skill(cls, skill_dir: Path) -> "EvalSuite":
        """Load the suite bundled with a skill at ``<skill_dir>/eval/``."""
        skill_dir = Path(skill_dir)
        eval_dir = skill_dir / "eval"
        if not eval_dir.is_dir():
            raise SuiteError(f"no eval/ directory found in skill: {skill_dir}")
        return cls.load(eval_dir)

    @classmethod
    def load(cls, eval_dir: Path) -> "EvalSuite":
        """Load a suite from an explicit ``eval/`` directory."""
        eval_dir = Path(eval_dir)
        suite_path = eval_dir / "suite.yaml"
        if not suite_path.is_file():
            raise SuiteError(f"no suite.yaml found in: {eval_dir}")

        try:
            raw = yaml.safe_load(suite_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SuiteError(f"invalid YAML in {suite_path}: {exc}") from exc
        if raw is None:
            raise SuiteError(f"empty suite.yaml: {suite_path}")
        if not isinstance(raw, dict):
            raise SuiteError(f"suite.yaml must be a mapping: {suite_path}")

        name = raw.get("name")
        if not name:
            raise SuiteError(f"suite.yaml missing required 'name': {suite_path}")

        evaluators = raw.get("evaluators") or []
        if not isinstance(evaluators, list):
            raise SuiteError(f"'evaluators' must be a list: {suite_path}")

        thresholds = raw.get("thresholds") or {}
        if not isinstance(thresholds, dict):
            raise SuiteError(f"'thresholds' must be a mapping: {suite_path}")

        examples = cls._discover_examples(eval_dir / "examples")

        return cls(
            name=name,
            description=raw.get("description", "") or "",
            skill=raw.get("skill", "") or "",
            evaluators=list(evaluators),
            thresholds=dict(thresholds),
            examples=examples,
            root=eval_dir,
        )

    @staticmethod
    def _discover_examples(examples_dir: Path) -> list[EvalExample]:
        """Read every example file in ``examples_dir`` and sort by id."""
        if not examples_dir.is_dir():
            return []

        collected: list[EvalExample] = []
        for path in sorted(examples_dir.iterdir()):
            if path.suffix.lower() not in _EXAMPLE_SUFFIXES:
                continue
            collected.extend(EvalSuite._parse_example_file(path))

        collected.sort(key=lambda e: e.id)
        return collected

    @staticmethod
    def _parse_example_file(path: Path) -> list[EvalExample]:
        """Parse one example file into one or more `EvalExample` objects."""
        text = path.read_text(encoding="utf-8")
        try:
            if path.suffix.lower() == ".json":
                data = json.loads(text)
            else:
                data = yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as exc:
            raise ExampleError(f"failed to parse example file {path.name}: {exc}") from exc

        if isinstance(data, dict):
            records = [data]
        elif isinstance(data, list):
            records = data
        else:
            raise ExampleError(
                f"example file {path.name} must contain an object or array, "
                f"got {type(data).__name__}"
            )

        examples: list[EvalExample] = []
        for record in records:
            examples.append(EvalSuite._build_example(record, path))
        return examples

    @staticmethod
    def _build_example(record: Any, path: Path) -> EvalExample:
        """Validate one raw example record and build an `EvalExample`."""
        if not isinstance(record, dict):
            raise ExampleError(
                f"example in {path.name} must be an object, got {type(record).__name__}"
            )
        ex_id = record.get("id")
        if not ex_id:
            raise ExampleError(f"example in {path.name} missing required 'id'")
        if "input" not in record or record.get("input") is None:
            raise ExampleError(f"example '{ex_id}' in {path.name} missing required 'input'")

        tags = record.get("tags") or []
        if not isinstance(tags, list):
            raise ExampleError(f"example '{ex_id}' in {path.name} has non-list 'tags'")

        return EvalExample(
            id=str(ex_id),
            input=str(record["input"]),
            expected_output=record.get("expected_output"),
            tags=[str(t) for t in tags],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "skill": self.skill,
            "evaluators": list(self.evaluators),
            "thresholds": dict(self.thresholds),
            "examples": [e.to_dict() for e in self.examples],
            "root": str(self.root) if self.root is not None else None,
        }
