"""End-to-end smoke: launch dashboard --demo, hit the two pages."""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import httpx


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_demo_smoke() -> None:
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "nuvel.cli", "dashboard",
         "--demo", "--no-open", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise AssertionError("dashboard did not become ready within 30s")

        r = httpx.get(f"http://127.0.0.1:{port}/", timeout=2.0)
        assert r.status_code == 200
        assert "nuvel" in r.text.lower()
        assert "b1aef763f4e48660" in r.text or "b1aef76" in r.text

        r = httpx.get(f"http://127.0.0.1:{port}/run/b1aef763", timeout=2.0)
        assert r.status_code == 200
        assert "thinking timeline" in r.text.lower()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
