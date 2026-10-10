from fastapi.testclient import TestClient
import pytest
import pyotp

from api.main import app
from services import login_anomaly as la
from services.auth_api import begin_mfa_enrollment, enable_mfa
from authentication import get_user_by_id


def test_strict_device_confirmation_requires_mfa_and_same_cookie(temp_db, monkeypatch):
    client = TestClient(app, headers={"Origin": "http://localhost:3000"})
    signup = client.post("/api/v1/auth/register", json={"name": "Device User", "email": "device@example.test", "password": "Device-password-123", "date_of_birth": "1990-01-01"})
    assert signup.status_code == 200
    uid = signup.json()["user"]["id"]
    enrollment = begin_mfa_enrollment(get_user_by_id(uid))
    recovery = enable_mfa(uid, pyotp.TOTP(enrollment["secret"]).now())
    client.post("/api/v1/auth/logout")
    monkeypatch.setenv("STOCKPILOT_LOGIN_ANOMALY_STRICT", "true")
    credentials = {"email": "device@example.test", "password": "Device-password-123"}
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200 and login.json()["mfa_required"]
    device = client.cookies.get(la.DEVICE_COOKIE)
    digest = la.token_device_hash(device)
    assert not la.device_confirmed(uid, digest)
    alert = la.anomalies_for_user(uid)[0]
    assert la.acknowledge_anomaly(uid, alert["id"])
    assert not la.device_confirmed(uid, digest)
    # Same UA and IP cannot replace possession of the random device cookie.
    original = device
    client.cookies.set(la.DEVICE_COOKIE, "b" * 64, domain="testserver.local", path="/api/v1/auth")
    wrong = client.post("/api/v1/auth/mfa/verify", json={"code": recovery[0]})
    assert wrong.status_code == 401
    client.cookies.set(la.DEVICE_COOKIE, original, domain="testserver.local", path="/api/v1/auth")
    confirmed = client.post("/api/v1/auth/mfa/verify", json={"code": recovery[0]})
    assert confirmed.status_code == 200
    assert la.device_confirmed(uid, digest)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/mfa/verify", json={"code": recovery[0]}).status_code == 401


def test_strict_detector_failure_cannot_return_clean_result(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_LOGIN_ANOMALY_STRICT", "true")
    def broken(*args, **kwargs):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(la, "_record_login_inner", broken)
    with pytest.raises(RuntimeError):
        la.record_login(1)
