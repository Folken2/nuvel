"""nuvel dashboard CLI subcommand."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

import uvicorn

from nuvel.dashboard.app import build_app
from nuvel.dashboard.loader import TraceLoader
from nuvel.traces_cli import _discover_trace_dirs


def _resolve_sources(args: argparse.Namespace) -> list[Path]:
    if args.demo:
        fixtures = Path(__file__).resolve().parent / "fixtures"
        return [fixtures]
    return _discover_trace_dirs(args.source)


def _port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def _cmd_dashboard(args: argparse.Namespace) -> int:
    sources = _resolve_sources(args)
    loader = TraceLoader(sources=sources)

    from nuvel.dashboard.watcher import RunWatcher
    watcher = None if args.demo else RunWatcher(sources=sources)

    app = build_app(loader, watcher=watcher)

    if _port_in_use(args.host, args.port):
        print(
            f"Port {args.port} is in use. Try `--port <other>`.",
            file=sys.stderr,
        )
        return 1

    url = f"http://{args.host}:{args.port}"
    print(f"nuvel dashboard -> {url}")
    if args.demo:
        print("  (demo mode: bundled fixtures; live updates disabled)")

    if args.open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "dashboard",
        help="Open the local web dashboard over your trace logs.",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="Bind address (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=8765,
                   help="Bind port (default 8765).")
    p.add_argument("--source", "-s", action="append", default=None,
                   help="Extra trace directory to scan (repeatable). "
                        "Same semantics as `nuvel traces --source`.")
    p.add_argument("--demo", action="store_true",
                   help="Load bundled demo fixtures instead of real traces.")
    p.add_argument("--no-open", dest="open_browser", action="store_false",
                   help="Don't open the browser automatically.")
    p.set_defaults(open_browser=True, func=_cmd_dashboard)
