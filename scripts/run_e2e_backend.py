"""Start the browser-test API with disposable encrypted account storage."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import sys
import tempfile


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="stockpilot-browser-") as directory:
        os.environ["STOCKPILOT_DATABASE_PATH"] = str(Path(directory) / "accounts.db")
        os.environ["STOCKPILOT_ENV"] = "test"
        os.environ["STOCKPILOT_PROVIDER_MODE"] = "OFFLINE_DEMO"
        os.environ["STOCKPILOT_DB_KEY_FILE"] = ""
        os.environ["STOCKPILOT_ENABLE_SCHEDULER"] = "false"
        os.environ["STOCKPILOT_REFRESH_INSTRUMENTS_ON_START"] = "false"
        for name in ("DB_ENCRYPTION_KEY", "JWT_SECRET", "MFA_SECRET", "FIELD_ENCRYPTION_KEY", "AUDIT_SECRET"):
            os.environ[f"STOCKPILOT_{name}"] = secrets.token_urlsafe(48)
        import uvicorn
        uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.getenv("STOCKPILOT_E2E_API_PORT", "8000")))


if __name__ == "__main__":
    main()
