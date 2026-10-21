import importlib.util
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest


_OFFLINE_MARKERS = (
    "Could not find a version that satisfies", "No matching distribution",
    "Failed to establish a new connection", "Temporary failure in name resolution",
    "Network is unreachable", "Failed to fetch", "dns error", "error sending request",
    "Connection refused", "Max retries exceeded",
)


def _build_wheel(pkg: pathlib.Path, out: pathlib.Path) -> None:
    if importlib.util.find_spec("pip") is not None:
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(pkg)]
    elif shutil.which("uv"):
        cmd = ["uv", "build", "--wheel", "--out-dir", str(out), str(pkg)]
    else:
        pytest.skip("neither pip nor uv is available to build the wheel")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        output = res.stdout + res.stderr
        if any(m in output for m in _OFFLINE_MARKERS):
            pytest.skip("cannot fetch the build backend (offline sandbox?)")
        raise AssertionError(f"wheel build failed:\n{output}")


def test_wheel_has_no_runtime_dependencies(tmp_path):
    pkg = pathlib.Path(__file__).parents[1]
    _build_wheel(pkg, tmp_path)
    whl = next(tmp_path.glob("nuvel_mcp-*.whl"))
    archive = zipfile.ZipFile(whl)
    meta = next(n for n in archive.namelist() if n.endswith("METADATA"))
    lines = archive.read(meta).decode().splitlines()
    requires = [line for line in lines if line.startswith("Requires-Dist:")]
    assert requires == [], requires  # zero runtime dependencies, and no extras either
    assert not [line for line in lines if line.startswith("Provides-Extra:")]


def test_wheel_carries_readme_and_license_text(tmp_path):
    pkg = pathlib.Path(__file__).parents[1]
    _build_wheel(pkg, tmp_path)
    archive = zipfile.ZipFile(next(tmp_path.glob("nuvel_mcp-*.whl")))
    names = archive.namelist()
    meta = archive.read(next(n for n in names if n.endswith("METADATA"))).decode()
    assert "Description-Content-Type: text/markdown" in meta
    assert "nuvel-mcp" in meta.split("\n\n", 1)[1]  # README body as long description
    licenses = [n for n in names if n.endswith("LICENSE") and ".dist-info/" in n]
    assert licenses and "MIT License" in archive.read(licenses[0]).decode()


def test_package_license_matches_the_repository_license():
    pkg = pathlib.Path(__file__).parents[1]
    repo_license = pkg.parents[1] / "LICENSE"
    if not repo_license.is_file():  # an unpacked sdist has no repository around it
        pytest.skip("not inside the repository")
    assert (pkg / "LICENSE").read_text() == repo_license.read_text()
