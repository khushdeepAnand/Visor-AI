#!/usr/bin/env python3
"""Cross-platform StockPilot setup for Windows and macOS.

Requires Python 3.12 and Node.js/npm compatible with frontend/package.json.
"""
from __future__ import annotations

import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
FRONTEND = ROOT / "frontend"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
FRONTEND_ENV = FRONTEND / ".env.local"
FRONTEND_ENV_EXAMPLE = FRONTEND / ".env.local.example"
PYTHON_VERSION = (3, 12)


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def find_python312() -> list[str] | None:
    def version_ok(cmd: list[str]) -> bool:
        try:
            r = subprocess.run(
                cmd + ["-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
                capture_output=True, text=True, check=True,
            )
            return r.stdout.strip() == "3.12"
        except (OSError, subprocess.CalledProcessError):
            return False

    if sys.version_info[:2] == PYTHON_VERSION:
        return [sys.executable]

    if os.name == "nt":
        py = shutil.which("py")
        if py and version_ok([py, "-3.12"]):
            return [py, "-3.12"]

    for name in ("python3.12", "python3", "python"):
        exe = shutil.which(name)
        if exe and version_ok([exe]):
            return [exe]
    return None


def find_npm() -> str | None:
    names = ["npm.cmd", "npm"] if os.name == "nt" else ["npm"]
    for name in names:
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def ensure_jwt_secret() -> None:
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ENV_EXAMPLE.read_text(encoding="utf-8")
    if not ENV.exists():
        ENV.write_text(text, encoding="utf-8")

    lines = text.splitlines()
    values = []
    kept = []
    for line in lines:
        if line.strip().startswith("STOCKPILOT_JWT_SECRET="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                values.append(value)
            continue
        kept.append(line)

    secret = values[-1] if values else secrets.token_urlsafe(48)
    kept.append(f"STOCKPILOT_JWT_SECRET={secret}")
    ENV.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print("JWT secret configured in .env.")


def ensure_frontend_env() -> None:
    if not FRONTEND_ENV.exists() and FRONTEND_ENV_EXAMPLE.exists():
        shutil.copy2(FRONTEND_ENV_EXAMPLE, FRONTEND_ENV)
        print("Created frontend/.env.local from its example.")

    if not FRONTEND_ENV.exists():
        return

    text = FRONTEND_ENV.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        if line.startswith("NEXT_PUBLIC_API_BASE=") and "127.0.0.1:8000" in line or \
           line.startswith("NEXT_PUBLIC_API_BASE=") and "localhost:8000" in line:
            out.append("NEXT_PUBLIC_API_BASE=")
        elif line.startswith("NEXT_PUBLIC_WS_BASE=") and ("127.0.0.1:8000" in line or "localhost:8000" in line):
            out.append("NEXT_PUBLIC_WS_BASE=")
        else:
            out.append(line)
    FRONTEND_ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    if os.name == "nt":
        return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "setup_stockpilot.ps1")], cwd=ROOT).returncode
    print(f"StockPilot AI cross-platform setup ({platform.system()})")
    py = find_python312()
    if not py:
        print("ERROR: Python 3.12 is required. Install Python 3.12 and run setup again.")
        return 1

    if not (VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).exists():
        if VENV.exists():
            print(f"ERROR: incomplete virtual environment at {VENV}. Remove it and run setup again.")
            return 1
        run(py + ["-m", "venv", str(VENV)])

    vpy = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--require-virtualenv", "--requirement", str(ROOT / "requirements-dev.lock")])

    npm = find_npm()
    if not npm:
        print("ERROR: npm was not found. Install Node.js 20.19+, 22.12+, or 24+ and retry.")
        return 1

    ensure_jwt_secret()
    ensure_frontend_env()
    run([npm, "ci", "--no-fund", "--no-audit"], FRONTEND)
    run([npm, "run", "build"], FRONTEND)
    run([npm, "exec", "--", "playwright", "install", "chromium"], FRONTEND)

    run([str(vpy), "-c", "import sys, fastapi, uvicorn; assert sys.version_info[:2] == (3,12)"])
    print("\nSETUP COMPLETE.")
    if os.name == "nt":
        print("Start with START_STOCKPILOT.bat")
    else:
        print("Start with ./START_STOCKPILOT.command or ./start_stockpilot.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
