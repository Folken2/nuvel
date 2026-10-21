import importlib.util
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest


# Only a failure to fetch the build backend (setuptools/wheel from
# build-system.requires) or to resolve a host name skips; anything else, such
# as an unrelated "Connection refused", fails the test.
_BACKEND_REQUIREMENTS = ("setuptools", "wheel")
_BACKEND_FETCH_MARKERS = (
    "Could not find a version that satisfies the requirement {req}",  # pip
    "No matching distribution found for {req}",  # pip
    "Failed to fetch: `https://pypi.org/simple/{req}/`",  # uv
)
_NAME_RESOLUTION_MARKERS = (
    "Temporary failure in name resolution",  # glibc
    "nodename nor servname provided",  # macOS
    "Name or service not known",
    "dns error",  # uv
)


def _is_backend_fetch_failure(output: str) -> bool:
    if any(m in output for m in _NAME_RESOLUTION_MARKERS):
        return True
    return any(m.format(req=req) in output
               for m in _BACKEND_FETCH_MARKERS for req in _BACKEND_REQUIREMENTS)


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
        if _is_backend_fetch_failure(output):
            pytest.skip("cannot fetch the build backend (offline sandbox?)")
        raise AssertionError(f"wheel build failed:\n{output}")


@pytest.mark.parametrize("output, offline", [
    ("ERROR: Could not find a version that satisfies the requirement setuptools>=77", True),
    ("ERROR: No matching distribution found for setuptools>=77", True),
    ("Failed to resolve requirements from `build-system.requires`\n"
     "  Caused by: Failed to fetch: `https://pypi.org/simple/setuptools/`", True),
    ("getaddrinfo failed: Temporary failure in name resolution", True),
    ("[Errno 8] nodename nor servname provided, or not known", True),
    ("dns error: failed to lookup address information", True),
    # Unrelated failures must fail the test, not skip it.
    ("ConnectionRefusedError: [Errno 61] Connection refused", False),
    ("Max retries exceeded with url: /simple/foo/", False),
    ("error: invalid command 'bdist_wheel'", False),
])
def test_only_build_backend_fetch_failures_skip(output, offline):
    assert _is_backend_fetch_failure(output) is offline


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
