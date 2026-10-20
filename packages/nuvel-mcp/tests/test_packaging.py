import importlib.util
import pathlib
import shutil
import subprocess
import sys
import zipfile


def _build_wheel(pkg: pathlib.Path, out: pathlib.Path) -> None:
    if importlib.util.find_spec("pip") is not None:
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out), str(pkg)]
    elif shutil.which("uv"):
        cmd = ["uv", "build", "--wheel", "--out-dir", str(out), str(pkg)]
    else:
        raise RuntimeError("neither pip nor uv is available to build the wheel")
    subprocess.run(cmd, check=True)


def test_wheel_has_no_runtime_dependencies(tmp_path):
    pkg = pathlib.Path(__file__).parents[1]
    _build_wheel(pkg, tmp_path)
    whl = next(tmp_path.glob("nuvel_mcp-*.whl"))
    archive = zipfile.ZipFile(whl)
    meta = next(n for n in archive.namelist() if n.endswith("METADATA"))
    lines = archive.read(meta).decode().splitlines()
    requires = [line for line in lines if line.startswith("Requires-Dist:")]
    # Only the optional `http` extra may pull in a dependency.
    assert requires and all("extra ==" in line for line in requires)
    assert any(line.startswith("Requires-Dist: mcp") for line in requires)
