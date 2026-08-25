"""nuvel doctor — diagnose nuvel install and the agent in cwd.

Inspired by `hermes doctor`. Prints a categorised checklist with
[OK] / [WARN] / [FAIL] markers and exits non-zero on any [FAIL].
Every check is wrapped so a single missing dependency cannot abort
the whole report.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

_PLACEHOLDER_HINTS = ("your_", "_here", "changeme", "xxx", "todo")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    def format(self) -> str:
        tag = f"[{self.status}]"
        line = f"  {tag:<7} {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return line


@dataclass
class AgentInfo:
    is_agent: bool = False
    framework: str | None = None
    reasons: list[str] = field(default_factory=list)


# ── helpers ─────────────────────────────────────────────────────────


def _safe(fn, name: str, *args, **kwargs) -> Check:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - never crash doctor
        return Check(name, FAIL, f"check raised {type(exc).__name__}: {exc}")


def parse_env_file(path: Path) -> dict[str, str]:
    """Best-effort .env parser — KEY=value, ignores comments and blanks."""
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return env
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def _is_placeholder(value: str) -> bool:
    if not value:
        return True
    low = value.lower()
    return any(hint in low for hint in _PLACEHOLDER_HINTS)


# ── install checks ──────────────────────────────────────────────────


def check_python_version() -> Check:
    major, minor = sys.version_info[:2]
    detail = f"{major}.{minor}.{sys.version_info.micro}"
    if (major, minor) >= (3, 11):
        return Check("Python version", OK, detail)
    return Check("Python version", FAIL, f"{detail} (need >= 3.11)")


def check_import(module: str, *, optional: bool = False, label: str | None = None) -> Check:
    name = label or f"import {module}"
    try:
        importlib.import_module(module)
        return Check(name, OK)
    except ImportError as exc:
        return Check(name, WARN if optional else FAIL, str(exc))


def check_command(cmd: str, *, optional: bool = False) -> Check:
    name = f"{cmd} on PATH"
    if shutil.which(cmd):
        return Check(name, OK)
    return Check(name, WARN if optional else FAIL, "not found")


def run_install_checks() -> list[Check]:
    checks: list[Check] = [_safe(check_python_version, "Python version")]
    # Core runtime deps from requirements.txt.
    for mod in ("yaml", "fastapi", "uvicorn", "dotenv", "litellm", "composio"):
        checks.append(_safe(check_import, f"import {mod}", mod))
    # Framework SDKs — optional (one per extra).
    for mod, label in (
        ("google.adk", "google-adk (adk extra)"),
        ("claude_agent_sdk", "claude-agent-sdk (claude extra)"),
        ("anthropic", "anthropic (managed extra)"),
    ):
        checks.append(_safe(check_import, label, mod, optional=True, label=label))
    checks.append(_safe(check_command, "git", "git"))
    return checks


# ── agent detection ─────────────────────────────────────────────────


def detect_agent(cwd: Path) -> AgentInfo:
    info = AgentInfo()
    req = cwd / "requirements.txt"
    has_entry = (
        (cwd / "agent.py").is_file()
        or (cwd / "server.py").is_file()
        or (cwd / "run_adk.py").is_file()
    )
    if not has_entry and cwd.is_dir():
        # ADK agents put agent.py inside a package subdir.
        for sub in cwd.iterdir():
            if sub.is_dir() and (sub / "agent.py").is_file():
                has_entry = True
                break
    if not req.is_file() or not has_entry:
        return info
    try:
        text = req.read_text(encoding="utf-8").lower()
    except OSError:
        return info
    info.is_agent = True
    if "google-adk" in text:
        info.framework = "adk"
    elif "claude-agent-sdk" in text:
        info.framework = "claude-agent-sdk"
    elif "anthropic" in text:
        info.framework = "anthropic-managed-agents"
    return info


# ── agent checks ────────────────────────────────────────────────────


_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "adk": ("OPENROUTER_API_KEY",),
    "claude-agent-sdk": ("ANTHROPIC_API_KEY",),
    "anthropic-managed-agents": ("ANTHROPIC_API_KEY",),
}

_GATEWAY_KEYS: dict[str, tuple[str, ...]] = {
    "slack": ("SLACK_BOT_TOKEN", "COMPOSIO_WEBHOOK_SECRET"),
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "teams_bridge": (
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET",
    ),
}


def _check_env_key(key: str, env: dict[str, str]) -> Check:
    if key not in env:
        return Check(key, FAIL, "missing from .env")
    if _is_placeholder(env[key]):
        return Check(key, FAIL, "looks like a placeholder")
    return Check(key, OK, "set")


def _check_docker_running() -> Check:
    if not shutil.which("docker"):
        return Check("Docker available", WARN, "docker CLI not on PATH")
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("Docker available", WARN, f"failed: {exc}")
    if r.returncode == 0:
        return Check("Docker available", OK, "daemon reachable")
    return Check("Docker available", WARN, "daemon not reachable")


def _find_pricing_entry(model: str, pricing: dict) -> Optional[str]:
    """Mirror cost_guard_plugin._find_pricing: exact then prefix-stripped match.

    Returns the matched pricing key (so we can show which entry covers the
    model), or None.
    """
    if not model:
        return None
    if model in pricing:
        return model
    parts = model.split("/")
    for i in range(len(parts)):
        candidate = "/".join(parts[i:])
        if candidate in pricing:
            return candidate
    return None


def _find_agent_plugins_dir(cwd: Path) -> Optional[Path]:
    """Locate the agent's plugins/ directory (sits inside the package dir)."""
    for sub in cwd.iterdir() if cwd.is_dir() else []:
        if sub.is_dir() and (sub / "plugins").is_dir():
            return sub / "plugins"
    return None


from nuvel._defaults import DEFAULT_FAST_MODEL, DEFAULT_REASONING_MODEL

_MODEL_DEFAULTS = {
    "FAST_MODEL": DEFAULT_FAST_MODEL,
    "REASONING_MODEL": DEFAULT_REASONING_MODEL,
}


def check_pricing_coverage(cwd: Path, env: dict[str, str]) -> list[Check]:
    """Verify pricing.json has entries for every active model.

    ADK agents resolve FAST_MODEL and REASONING_MODEL with hard-coded
    fallbacks in config/llm.py — so a missing .env entry isn't a failure
    on its own; the fallback model is what actually runs and is what
    pricing must cover.

    Without coverage, llm_response.cost_usd and run_end.total_cost_usd
    stay null and the traces command center can't show cost.
    """
    plugins = _find_agent_plugins_dir(cwd)
    if plugins is None:
        return [Check("pricing.json coverage", WARN, "no plugins/ dir found")]

    pricing_path = plugins / "pricing.json"
    if not pricing_path.is_file():
        return [Check("pricing.json coverage", WARN, f"missing {pricing_path}")]

    try:
        raw = json.loads(pricing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [Check("pricing.json coverage", FAIL, f"parse error: {exc}")]

    pricing = {k: v for k, v in raw.items() if not k.startswith("_")}
    checks: list[Check] = []
    for var, default in _MODEL_DEFAULTS.items():
        model = env.get(var) or default
        source = ".env" if env.get(var) else "default"
        matched = _find_pricing_entry(model, pricing)
        name = f"pricing.json: {var}"
        if matched:
            via = "" if matched == model else f" via {matched!r}"
            checks.append(Check(name, OK, f"{model} ({source}){via}"))
        else:
            checks.append(Check(
                name, WARN,
                f"no entry for {model!r} ({source}) — costs will be null"
            ))
    return checks


def _composio_in_use(cwd: Path) -> bool:
    for candidate in cwd.rglob("composio_mcp.py"):
        if ".venv" not in candidate.parts and "site-packages" not in candidate.parts:
            return True
    return False


def run_agent_checks(cwd: Path, info: AgentInfo) -> list[Check]:
    checks: list[Check] = []
    env_path = cwd / ".env"
    if env_path.is_file():
        checks.append(Check(".env file", OK, str(env_path.name)))
    else:
        example = cwd / ".env.example"
        hint = " (copy .env.example)" if example.is_file() else ""
        checks.append(Check(".env file", FAIL, f"missing{hint}"))

    env = parse_env_file(env_path)
    required = _REQUIRED_KEYS.get(info.framework or "", ())
    for key in required:
        checks.append(_safe(_check_env_key, key, key, env))

    if _composio_in_use(cwd):
        checks.append(_safe(_check_env_key, "COMPOSIO_API_KEY", "COMPOSIO_API_KEY", env))

    gateways_dir = cwd / "gateways"
    if not gateways_dir.is_dir():
        # Templates put gateways/ inside the package dir.
        for sub in cwd.iterdir() if cwd.is_dir() else []:
            cand = sub / "gateways"
            if cand.is_dir():
                gateways_dir = cand
                break
    if gateways_dir.is_dir():
        for stem, keys in _GATEWAY_KEYS.items():
            if (gateways_dir / f"{stem}.py").is_file():
                for key in keys:
                    checks.append(
                        _safe(_check_env_key, f"gateway:{stem} {key}", key, env)
                    )

    if (cwd / "Dockerfile").is_file():
        checks.append(_safe(_check_docker_running, "Docker available"))

    if info.framework == "adk":
        try:
            checks.extend(check_pricing_coverage(cwd, env))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(
                "pricing.json coverage", FAIL,
                f"check raised {type(exc).__name__}: {exc}",
            ))

    return checks


# ── ACP adapter (--with-acp) ────────────────────────────────────────


def detect_acp_package(cwd: Path) -> Optional[str]:
    """Return the package name of a --with-acp agent (has ``<pkg>/acp/__main__.py``)."""
    if not cwd.is_dir():
        return None
    for sub in sorted(cwd.iterdir()):
        if sub.is_dir() and (sub / "acp" / "__main__.py").is_file():
            return sub.name
    return None


def zed_config_snippet(cwd: Path, pkg: str, *, python: str | None = None) -> str:
    """A ready-to-paste Zed ``agent_servers`` entry for this ACP agent."""
    snippet = {
        "agent_servers": {
            pkg.replace("_", "-"): {
                "command": python or sys.executable,
                "args": ["-m", f"{pkg}.acp"],
                "cwd": str(cwd),
            }
        }
    }
    return json.dumps(snippet, indent=2)


def check_acp_handshake(cwd: Path, pkg: str, timeout: float = 20.0) -> Check:
    """Smoke-test the ACP stdio protocol: send ``initialize``, expect a reply.

    Runs the adapter with ``DEV_MODE=true`` so it uses in-memory services and
    doesn't need a database — this checks the JSON-RPC handshake, not config.
    """
    name = "ACP stdio handshake"
    cmd = [sys.executable, "-m", f"{pkg}.acp"]
    env = {**os.environ, "DEV_MODE": "true", "LOG_LEVEL": "ERROR"}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except OSError as exc:
        return Check(name, FAIL, f"could not launch: {exc}")

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": 2, "clientCapabilities": {}},
        }
    )
    holder: dict[str, str] = {}

    def _read_line() -> None:
        try:
            holder["line"] = proc.stdout.readline() if proc.stdout else ""
        except Exception as exc:  # noqa: BLE001
            holder["error"] = str(exc)

    try:
        if proc.stdin:
            proc.stdin.write(request + "\n")
            proc.stdin.flush()
    except OSError:
        pass  # process may have died on import; handled via readline/EOF below

    reader = threading.Thread(target=_read_line, daemon=True)
    reader.start()
    reader.join(timeout)

    def _stop() -> None:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    if reader.is_alive():
        _stop()
        return Check(name, WARN, f"no response within {timeout:.0f}s (are agent deps installed?)")

    line = holder.get("line", "")
    stderr_tail = ""
    if not line and proc.stderr:
        try:
            stderr_tail = (proc.stderr.read() or "")[-300:]
        except Exception:  # noqa: BLE001
            stderr_tail = ""
    _stop()

    if not line:
        low = stderr_tail.lower()
        if "modulenotfound" in low or "importerror" in low or "no module named" in low:
            return Check(name, WARN, "agent deps not installed (pip install -r requirements.txt)")
        detail = stderr_tail.strip().replace("\n", " ")[:120] or "no output"
        return Check(name, FAIL, f"no initialize response — {detail}")

    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return Check(name, FAIL, f"invalid JSON-RPC line: {line.strip()[:80]}")

    result = msg.get("result") or {}
    if "protocolVersion" in result and "agentCapabilities" in result:
        return Check(name, OK, f"initialize → protocolVersion {result['protocolVersion']}")
    return Check(name, FAIL, f"unexpected initialize response: {line.strip()[:80]}")


def run_acp_checks(cwd: Path, pkg: str) -> list[Check]:
    return [_safe(check_acp_handshake, "ACP stdio handshake", cwd, pkg)]


# ── orchestration ───────────────────────────────────────────────────


def _print_section(title: str, checks: Iterable[Check]) -> tuple[int, int, int]:
    print(f"{title}")
    ok = warn = fail = 0
    for c in checks:
        print(c.format())
        if c.status == OK:
            ok += 1
        elif c.status == WARN:
            warn += 1
        else:
            fail += 1
    print()
    return ok, warn, fail


def run_doctor(cwd: Path | None = None) -> int:
    cwd = (cwd or Path.cwd()).resolve()
    print("nuvel doctor")
    print(f"cwd: {cwd}")
    print()

    install = run_install_checks()
    ok1, warn1, fail1 = _print_section("Install", install)

    info = detect_agent(cwd)
    if info.is_agent:
        agent_checks = run_agent_checks(cwd, info)
        title = f"Agent ({info.framework or 'unknown'})"
        ok2, warn2, fail2 = _print_section(title, agent_checks)
    else:
        ok2 = warn2 = fail2 = 0
        print("No generated agent detected in cwd (skipping agent checks).")
        print()

    ok3 = warn3 = fail3 = 0
    acp_pkg = detect_acp_package(cwd)
    if acp_pkg:
        ok3, warn3, fail3 = _print_section("ACP adapter", run_acp_checks(cwd, acp_pkg))
        print("  Zed — add to settings.json (agent_servers):")
        for line in zed_config_snippet(cwd, acp_pkg).splitlines():
            print(f"    {line}")
        print()

    total_ok = ok1 + ok2 + ok3
    total_warn = warn1 + warn2 + warn3
    total_fail = fail1 + fail2 + fail3
    print("Summary")
    print(f"  {total_ok} ok, {total_warn} warn, {total_fail} fail")
    return 1 if total_fail else 0
