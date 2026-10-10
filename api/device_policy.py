"""HTTP boundary for cookie-bound, independently confirmed login devices."""
from typing import Any
from fastapi import HTTPException, Request, Response
from api.deps import _secure_cookie
from services.login_anomaly import DEVICE_COOKIE, NEW_DEVICE_WINDOW_DAYS, device_token, record_login, strict_mode, token_device_hash, device_confirmed


def observe_login_device(user: dict[str, Any], request: Request) -> tuple[str, dict[str, Any]]:
    token = device_token(request)
    try:
        anomaly = record_login(int(user["id"]), user_agent=request.headers.get("user-agent", ""),
                               ip=request.client.host if request.client else None, device_hash=token_device_hash(token))
    except Exception as exc:
        if strict_mode():
            raise HTTPException(status_code=503, detail="Device verification is unavailable; retry later.") from exc
        anomaly = {"anomalies": [], "device_hash": token_device_hash(token), "impossible_travel": False}
    return token, anomaly


def set_device_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(DEVICE_COOKIE, token, httponly=True, secure=_secure_cookie(request), samesite="lax",
                        max_age=NEW_DEVICE_WINDOW_DAYS * 86400, path="/api/v1/auth")


def enforce_device_policy(user: dict[str, Any], anomaly: dict[str, Any]) -> None:
    if strict_mode() and (anomaly.get("impossible_travel") or not device_confirmed(int(user["id"]), str(anomaly.get("device_hash", "")))):
        raise HTTPException(status_code=403, detail="Independent MFA is required to confirm this sign-in device. Enroll MFA through your existing authenticated session.")
