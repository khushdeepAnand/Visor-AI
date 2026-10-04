"""Install the StockPilot pre-commit secret gate into .git/hooks."""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

HOOK_NAME = "pre-commit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--scripts-dir", type=Path, default=Path(__file__).resolve().parent / "hooks")
    args = parser.parse_args()

    root = args.root.resolve()
    git_dir = root / ".git"
    if git_dir.is_file():
        git_file = git_dir.read_text(encoding="utf-8").strip()
        if git_file.startswith("gitdir:"):
            git_dir = (root / git_file.split(":", 1)[1].strip()).resolve()
    if not git_dir.is_dir():
        print(f"No git repository found at {root}. Run inside a repo root.", file=sys.stderr)
        return 1

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    source = args.scripts_dir / HOOK_NAME
    if not source.is_file():
        print(f"Missing hook source: {source}", file=sys.stderr)
        return 1

    destination = hooks_dir / HOOK_NAME
    destination.write_bytes(source.read_bytes())
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"Installed {HOOK_NAME} hook -> {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())