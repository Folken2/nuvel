"""Plan/apply installer that copies a hub skill into a client's skills directory.

Used by the ``install_skill`` MCP tool. Stdlib only. The contract:

- Planning (``confirm=False``) is read-only and reports what would happen.
- Applying writes into a staging directory next to the destination, verifies
  it, then swaps it into place. An existing destination is moved to
  ``<dest>.backup`` first; an existing backup is never overwritten.
- Symlinked or non-directory targets/destinations are refused (``blocked``).
- A failed write rolls back and reports ``failed`` — never success.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def _skipped(name: str) -> bool:
    """Hidden files and bytecode caches are never part of an installed skill."""
    return name.startswith(".") or name == "__pycache__" or name.endswith(".pyc")


def read_tree(directory: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    """Read every regular file under ``directory`` -> (contents, modes).

    Keys are POSIX relative paths. Symlinks (files or dirs) are skipped so a
    hub checkout can't smuggle files from outside the skill directory.
    """
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for root, dirs, names in os.walk(directory):
        root_path = Path(root)
        dirs[:] = sorted(
            d for d in dirs if not _skipped(d) and not (root_path / d).is_symlink()
        )
        for name in sorted(names):
            path = root_path / name
            if _skipped(name) or path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(directory).as_posix()
            files[rel] = path.read_bytes()
            modes[rel] = path.stat().st_mode
    return files, modes


def _skill_md_ok(directory: Path) -> bool:
    skill_md = directory / "SKILL.md"
    return skill_md.is_file() and skill_md.stat().st_size > 0


def _refusal(path: Path, label: str) -> str | None:
    if path.is_symlink():
        return f"{label} {path} is a symlink; refusing to install through it"
    if path.exists() and not path.is_dir():
        return f"{label} {path} exists and is not a directory"
    return None


def install_skill(
    name: str,
    source_dir: Path,
    skill_md: str,
    target_dir: str,
    confirm: bool,
) -> dict:
    """Plan (or, with ``confirm``, apply) installing a skill into ``target_dir``.

    ``skill_md`` is the SKILL.md body as read by the loader; every other file
    under ``source_dir`` is copied alongside it.
    """
    target = Path(target_dir).expanduser()
    dest = target / name
    backup = dest.with_name(dest.name + ".backup")
    result = {
        "status": "blocked",
        "action": None,
        "skill": name,
        "target": str(dest),
        "target_dir": str(target),
        "files": 0,
        "paths": [],
        "backup": None,
        "error": None,
    }

    def blocked(error: str) -> dict:
        result.update(status="blocked", error=error)
        return result

    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return blocked(f"Unsafe skill name for a directory: {name!r}")
    for path, label in ((target, "Target directory"), (dest, "Destination")):
        reason = _refusal(path, label)
        if reason:
            return blocked(reason)

    try:
        files, modes = read_tree(source_dir)
    except OSError as exc:
        return blocked(f"Cannot read skill files from {source_dir}: {exc}")
    files["SKILL.md"] = skill_md.encode("utf-8")
    files = dict(sorted(files.items()))
    result.update(files=len(files), paths=list(files))

    if not dest.exists():
        action = "install"
    else:
        try:
            existing, _ = read_tree(dest)
        except OSError as exc:
            return blocked(f"Cannot read existing destination {dest}: {exc}")
        action = "noop" if existing == files else "update"
    result["action"] = action

    if action == "update":
        if os.path.lexists(backup):
            return blocked(
                f"Backup {backup} already exists; refusing to overwrite it. "
                "Move or delete it, then retry."
            )
        result["backup"] = str(backup)

    if not confirm:
        result["status"] = "planned"
        return result
    if action == "noop":
        result["status"] = "noop"
        return result

    staging: Path | None = None
    backed_up = swapped = False
    try:
        target.mkdir(parents=True, exist_ok=True)
        reason = _refusal(target, "Target directory")
        if reason:
            return blocked(reason)
        staging = Path(tempfile.mkdtemp(prefix=f".{name}.staging-", dir=target))
        for rel, data in files.items():
            out = staging / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            exec_bits = modes.get(rel, 0) & 0o111
            if exec_bits:
                out.chmod(out.stat().st_mode | exec_bits)
        staged, _ = read_tree(staging)
        if staged != files:
            raise OSError("staged files do not match the source skill")
        if dest.exists():
            os.rename(dest, backup)
            backed_up = True
        os.rename(staging, dest)
        staging = None
        swapped = True
        if not _skill_md_ok(dest):
            raise OSError(f"{dest / 'SKILL.md'} is missing or empty after install")
    except Exception as exc:  # noqa: BLE001 - any failure must roll back
        error = f"Install failed: {exc}"
        result["backup"] = None
        if swapped:
            shutil.rmtree(dest, ignore_errors=True)
        if backed_up:
            try:
                os.rename(backup, dest)
            except OSError as restore_exc:
                error += f"; restoring {backup} -> {dest} also failed: {restore_exc}"
                result["backup"] = str(backup)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        result.update(status="failed", error=error)
        return result

    result["status"] = "installed"
    return result
