"""Browser verification must not reuse personal account data or credentials."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_database_path_override_is_shared_by_authentication(tmp_path):
    destination = tmp_path / "browser-test" / "accounts.db"
    environment = {**os.environ, "STOCKPILOT_DATABASE_PATH": str(destination)}
    result = subprocess.run(
        [sys.executable, "-c", "import database, authentication; "
         "from pathlib import Path; import os; "
         "expected = Path(os.environ['STOCKPILOT_DATABASE_PATH']); "
         "assert Path(database.DATABASE) == expected; "
         "assert authentication.DATABASE_PATH == expected; "
         "assert Path(database.DATABASE_DIR) == expected.parent"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_browser_servers_must_not_reuse_running_personal_servers():
    config = (ROOT / "frontend/playwright.config.ts").read_text()
    assert config.count("reuseExistingServer: false") == 2
    assert "scripts/run_e2e_backend.py" in config
