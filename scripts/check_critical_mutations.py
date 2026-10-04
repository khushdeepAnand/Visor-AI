"""Execute targeted auth, ownership and forecast-gate mutants in disposable copies.

This small cross-platform mutation gate supplements (does not replace) a broad
mutmut campaign. Only assertion failures count as killed, not collection errors.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from defusedxml import ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_hygiene import collect_allowlisted

MUTANTS = (
    ("password-verification-bypass", "authentication.py", "password_matches = verify_password(password, selected_hash)", "password_matches = True", "tests/test_authentication.py::test_login_wrong_password_gives_generic_message"),
    ("portfolio-ownership-bypass", "database.py", "FROM portfolio\n\n        WHERE user_id = ?", "FROM portfolio\n\n        WHERE ? IS NOT NULL", "tests/test_idor_cross_user_access.py::test_cross_user_portfolio_isolation"),
    ("coverage-gate-bypass", "forecasting/promotion_gate.py", "passed = abs(gap) <= tolerance", "passed = True", "tests/test_promotion_gate.py::test_check_coverage_gate"),
)


def main() -> int:
    environment = os.environ.copy()
    environment["STOCKPILOT_ENV"] = "test"
    environment.pop("PYTHONPATH", None)
    killed = 0
    with tempfile.TemporaryDirectory(prefix="stockpilot-mutations-") as temporary:
        copy = Path(temporary)
        for source in collect_allowlisted(ROOT):
            target = copy / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for name, filename, before, after, test in MUTANTS:
            target = copy / filename
            original = target.read_text(encoding="utf-8")
            if original.count(before) != 1:
                raise RuntimeError(f"Mutation target changed: {name}; update and review the gate")
            command = [sys.executable, "-m", "pytest", test, "-q", "-p", "no:randomly", "--junitxml=mutation.xml"]
            subprocess.run(command, cwd=copy, env=environment, check=True, timeout=120)
            target.write_text(original.replace(before, after, 1), encoding="utf-8")
            try:
                result = subprocess.run(command, cwd=copy, env=environment, check=False, timeout=120)
                report = ET.parse(copy / "mutation.xml").getroot()
                if report is None:
                    raise RuntimeError("Mutation report is empty.")
                failures = len(report.findall(".//failure"))
                errors = len(report.findall(".//error"))
                if result.returncode == 1 and failures > 0 and errors == 0:
                    killed += 1
                    print(f"KILLED: {name}")
                else:
                    print(f"SURVIVED or invalid execution: {name}")
            finally:
                target.write_text(original, encoding="utf-8")
                # Bytecode can otherwise retain a same-size/same-second mutant.
                for cache in copy.rglob("__pycache__"):
                    shutil.rmtree(cache)
    print(f"Critical mutation gate: {killed}/{len(MUTANTS)} killed")
    return 0 if killed == len(MUTANTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
