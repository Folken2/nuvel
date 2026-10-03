"""package-data must ship every template and skill file, dotfiles included.

setuptools expands package-data patterns with ``glob(..., recursive=True)``,
where ``*`` and ``**`` never match a leading dot. A pattern list that only
says ``templates/**/*`` silently drops ``.env.example``, ``.dockerignore``,
``.gitignore`` and ``.gitkeep`` from the wheel, so scaffolds made from an
installed nuvel came out without them.
"""

from __future__ import annotations

import glob
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "nuvel"


def _packaged_files() -> set[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["nuvel"]
    files: set[str] = set()
    for pattern in patterns:
        for match in glob.glob(str(PACKAGE / pattern), recursive=True):
            if os.path.isfile(match):
                files.add(os.path.relpath(match, PACKAGE))
    return files


def _shipped_data_files() -> set[str]:
    wanted: set[str] = set()
    for backend in (PACKAGE / "backends").iterdir():
        for sub in ("templates", "templates_overlays", "skills"):
            base = backend / sub
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for fname in filenames:
                    if fname.endswith(".pyc"):
                        continue
                    wanted.add(os.path.relpath(os.path.join(dirpath, fname), PACKAGE))
    return wanted


def test_package_data_covers_every_template_and_skill_file():
    missing = sorted(_shipped_data_files() - _packaged_files())
    assert not missing, f"package-data patterns miss: {missing}"


def test_adk_template_dotfiles_are_packaged():
    packaged = _packaged_files()
    for rel in ("backends/adk/templates/.env.example", "backends/adk/templates/.dockerignore"):
        assert rel in packaged
