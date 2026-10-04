"""Command-line configuration doctor.

Usage
-----
    python scripts/doctor_setup.py              # read the process environment
    python scripts/doctor_setup.py --env-file .env
    python scripts/doctor_setup.py --json

Exit codes
----------
    0  everything the app needs is configured (warnings allowed)
    1  at least one check is action_required

Run this before reporting that sign-in or market data is broken. It reads only
variable names and presence; no secret value is printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.setup_doctor import STATUS_ACTION, render_text_report, run_diagnostics  # noqa: E402


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file without requiring python-dotenv."""
    values: dict[str, str] = {}
    if not path.exists():
        raise SystemExit("env file not found: %s" % path)
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose StockPilot configuration.")
    parser.add_argument("--env-file", help="Read variables from this dotenv file instead of the process environment.")
    parser.add_argument(
        "--frontend-env-file",
        help="Optionally merge a frontend .env.local file so NEXT_PUBLIC_* values are checked too.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    if args.env_file:
        environment: dict[str, str] = parse_env_file(Path(args.env_file))
    else:
        environment = dict(os.environ)
    if args.frontend_env_file:
        environment.update(parse_env_file(Path(args.frontend_env_file)))

    if args.json:
        print(json.dumps(run_diagnostics(environment), indent=2))
    else:
        print(render_text_report(environment))

    return 1 if run_diagnostics(environment)["status"] == STATUS_ACTION else 0


if __name__ == "__main__":
    raise SystemExit(main())
