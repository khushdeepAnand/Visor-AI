from fastapi.testclient import TestClient

from api.main import app
from authentication import register_user
from database import get_connection
from services.auth_api import mfa_status
from services.webauthn import _ensure_table


def test_passkey_only_account_is_challenged_and_cookie_reaches_webauthn(temp_db):
    assert register_user("Passkey user", "passkey@example.test", "GoodPassword123!", "1990-01-01")[0]
    _ensure_table()
    conn = get_connection()
    uid = conn.execute("SELECT id FROM users").fetchone()[0]
    conn.execute("INSERT INTO webauthn_credentials(user_id,credential_id,public_key) VALUES(?,'credential','public-key')", (uid,))
    conn.commit()
    conn.close()
    assert mfa_status(uid)["enabled"] is True
    with TestClient(app) as client:
        result = client.post("/api/v1/auth/login", json={"email": "passkey@example.test", "password": "GoodPassword123!"})
        assert result.status_code == 200
        assert result.json()["mfa_required"] is True
        assert "stockpilot_session=" not in result.headers.get("set-cookie", "")
        assert "Path=/api/v1/auth;" in result.headers["set-cookie"]
