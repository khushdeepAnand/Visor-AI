#!/usr/bin/env python3
"""Cross-platform StockPilot launcher for Windows and macOS."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
FRONTEND = ROOT / "frontend"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return 200 <= int(r.status) < 400
    except Exception:
        return False


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def stop_process(p: subprocess.Popen[bytes] | None) -> None:
    if not p or p.poll() is not None:
        return
    try:
        if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
            p.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        else:
            kill_process_group = getattr(os, "killpg")
            kill_process_group(p.pid, signal.SIGTERM)
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                kill_process_group(p.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def main() -> int:
    if os.name == "nt":
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "start_stockpilot.ps1")], cwd=ROOT).returncode
    if not VENV_PYTHON.is_file():
        print("Setup is required. Run setup_stockpilot.py first.")
        return 1
    if not (FRONTEND / "node_modules").is_dir():
        print("Frontend dependencies are missing. Run setup_stockpilot.py first.")
        return 1
    if not (ROOT / ".env").is_file():
        print(".env is missing. Run setup_stockpilot.py first.")
        return 1

    for port in (8000, 3000):
        if not port_free(port):
            print(f"ERROR: port {port} is already in use.")
            return 2

    backend = frontend = None
    try:
        print("Starting StockPilot backend and frontend...")
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
        preexec = getattr(os, "setsid", None) if os.name != "nt" else None

        backend = subprocess.Popen(
            [str(VENV_PYTHON), "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"],
            cwd=ROOT, creationflags=creationflags, preexec_fn=preexec,
        )
        frontend = subprocess.Popen(
            [npm_executable(), "run", "start", "--", "--hostname", "127.0.0.1", "--port", "3000"],
            cwd=FRONTEND, creationflags=creationflags, preexec_fn=preexec,
        )

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError(f"Backend exited with code {backend.returncode}.")
            if frontend.poll() is not None:
                raise RuntimeError(f"Frontend exited with code {frontend.returncode}.")
            if ready("http://127.0.0.1:8000/api/v1/ready") and ready("http://127.0.0.1:3000/"):
                print("\nStockPilot is ready: http://127.0.0.1:3000")
                print("Press Ctrl+C to stop StockPilot.")
                while True:
                    if backend.poll() is not None:
                        return backend.returncode or 1
                    if frontend.poll() is not None:
                        return frontend.returncode or 1
                    time.sleep(1)
            time.sleep(0.5)
        raise RuntimeError("Readiness timed out after 120 seconds.")
    except KeyboardInterrupt:
        print("\nStopping StockPilot...")
        return 130
    except Exception as exc:
        print(f"START FAILED: {exc}")
        return 1
    finally:
        stop_process(frontend)
        stop_process(backend)


if __name__ == "__main__":
    raise SystemExit(main())
