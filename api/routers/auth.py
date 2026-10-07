"""Router for the auth domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from services.admin_registry import effective_role
from database import delete_user_data, record_audit_event
from services.compliance import RESEARCH_ACKNOWLEDGMENT_VERSION, research_acknowledgment_required
from services.auth_api import (
    begin_mfa_enrollment,
    complete_mfa_challenge,
    disable_mfa,
    enable_mfa,
    issue_mfa_challenge,
    mfa_status,
    regenerate_recovery_codes,
    revoke_other_sessions,
)


router = APIRouter()
MFA_CHALLENGE_COOKIE = "stockpilot_mfa_challenge"


class ResearchAcknowledgmentPayload(BaseModel):
    version: str = Field(min_length=1, max_length=40)
    accepted: Literal[True]


class DeleteAccountPayload(BaseModel):
    confirmation: Literal["DELETE"]


class MfaCodePayload(BaseModel):
    code: str = Field(min_length=6, max_length=32)


def _set_mfa_challenge_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=MFA_CHALLENGE_COOKIE,
        value=token,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        max_age=300,
        path="/api/v1/auth",
    )


def _clear_mfa_challenge_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        MFA_CHALLENGE_COOKIE,
        path="/api/v1/auth",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(MFA_CHALLENGE_COOKIE, path="/api/v1/auth/mfa", secure=_secure_cookie(request), httponly=True, samesite="lax")



@router.post("/api/v1/auth/register")
def register_endpoint(payload: Registration, response: Response, request: Request) -> dict[str, Any]:
    ok, result = register(payload.name, payload.email, payload.password, payload.date_of_birth)
    if not ok or not isinstance(result, dict):
        raise HTTPException(status_code=400, detail=str(result))
    _set_session_cookie(response, request, create_access_token(result, request=request))
    return {"user": result}


@router.post("/api/v1/auth/login")
def login_endpoint(payload: Credentials, response: Response, request: Request) -> dict[str, Any]:
    ok, result = authenticate(payload.email, payload.password)
    if not ok or not isinstance(result, dict):
        raise HTTPException(status_code=401, detail=str(result))
    # Login anomaly detection (new-device / impossible-travel) — best-effort,
    # never blocks authentication; results are surfaced on the response.
    try:
        from services.login_anomaly import record_login, strict_mode
        client_ip = request.client.host if request.client else None
        anomaly = record_login(
            int(result["id"]),
            user_agent=request.headers.get("user-agent", ""),
            ip=client_ip,
        )
    except Exception:
        anomaly = {"new_device": False, "impossible_travel": False, "anomalies": []}
    if mfa_status(int(result["id"]))["enabled"]:
        challenge = issue_mfa_challenge(result, next_path=payload.next)
        _set_mfa_challenge_cookie(response, request, challenge)
        return {"mfa_required": True, "anomalies": anomaly["anomalies"]}
    _set_session_cookie(response, request, create_access_token(result, request=request))
    return {"user": result, "anomalies": anomaly["anomalies"]}


@router.post("/api/v1/auth/mfa/verify")
def mfa_verify_endpoint(
    payload: MfaCodePayload,
    response: Response,
    request: Request,
    challenge: str | None = Cookie(default=None, alias=MFA_CHALLENGE_COOKIE),
) -> dict[str, Any]:
    if not challenge:
        raise HTTPException(status_code=401, detail="The verification challenge is invalid or expired.")
    try:
        user, next_path, method = complete_mfa_challenge(challenge, payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookie(response, request, create_access_token(user, request=request))
    _clear_mfa_challenge_cookie(response, request)
    return {"user": user, "next": next_path, "method": method}


@router.get("/api/v1/auth/mfa")
def mfa_status_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return mfa_status(int(user["id"]))


@router.post("/api/v1/auth/mfa/setup")
def mfa_setup_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, str]:
    try:
        return begin_mfa_enrollment(user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/auth/mfa/enable")
def mfa_enable_endpoint(payload: MfaCodePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        recovery_codes = enable_mfa(int(user["id"]), payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"enabled": True, "recovery_codes": recovery_codes}


@router.post("/api/v1/auth/mfa/recovery-codes")
def mfa_recovery_codes_endpoint(payload: MfaCodePayload, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    try:
        recovery_codes = regenerate_recovery_codes(int(user["id"]), payload.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"recovery_codes": recovery_codes}


@router.post("/api/v1/auth/mfa/disable")
def mfa_disable_endpoint(
    payload: MfaCodePayload,
    user: dict[str, Any] = Depends(current_user),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    token = _bearer_token(authorization) or session
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        disable_mfa(int(user["id"]), payload.code)
        revoke_other_sessions(int(user["id"]), token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"enabled": False}


@router.post("/api/v1/auth/logout")
def logout_endpoint(
    response: Response,
    request: Request,
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    token = _bearer_token(authorization) or session
    if token:
        try:
            revoke_token(token)
        except ValueError:
            pass
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.get("/api/v1/auth/sessions")
def sessions_endpoint(
    user: dict[str, Any] = Depends(current_user),
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    return {"items": list_sessions(user["id"], _bearer_token(authorization) or session)}


@router.delete("/api/v1/auth/sessions/{session_id}")
def revoke_session_endpoint(session_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    if not revoke_session(user["id"], session_id):
        raise HTTPException(status_code=404, detail="Session not found or already revoked.")
    return {"revoked": True}


@router.post("/api/v1/auth/logout-all")
def logout_all_endpoint(response: Response, request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, bool]:
    revoke_all_sessions(user["id"])
    response.delete_cookie(COOKIE_NAME, path="/", secure=_secure_cookie(request), httponly=True, samesite="lax")
    return {"revoked": True}


@router.post("/api/v1/auth/research-acknowledgment")
def research_acknowledgment_endpoint(
    payload: ResearchAcknowledgmentPayload,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    if payload.version != RESEARCH_ACKNOWLEDGMENT_VERSION:
        raise HTTPException(status_code=409, detail="The research acknowledgment has changed. Reload and review it again.")
    record_audit_event(
        user["id"],
        "research_disclaimer_acknowledged",
        entity_type="compliance",
        entity_id=payload.version,
        details={"version": payload.version},
    )
    return {"acknowledged": True, "version": payload.version}


@router.delete("/api/v1/auth/account")
def delete_account_endpoint(
    payload: DeleteAccountPayload,
    response: Response,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, bool]:
    del payload
    if not delete_user_data(user["id"]):
        raise HTTPException(status_code=404, detail="Account not found.")
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_secure_cookie(request),
        httponly=True,
        samesite="lax",
    )
    return {"deleted": True}


@router.get("/api/v1/auth/me")
def me_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """The role/identity view presented to the logged-in user.

    Session-internal material (token versions, verification numbers) is never
    published here; the sessions list is the display surface for management.
    """
    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "created_at": user.get("created_at"),
            "auth_provider": user.get("auth_provider", "password"),
            "connected_providers": user.get("connected_providers", []),
            "account_status": user.get("account_status", "active"),
            "mfa_enabled": bool(user.get("mfa_enabled", False)),
            "research_acknowledgment_required": research_acknowledgment_required(user["id"]),
            "role": effective_role(user),
        }
    }


@router.get("/api/v1/auth/oauth/status")
def oauth_status_endpoint() -> dict[str, Any]:
    """Configuration presence only; no provider identifier or secret is returned."""
    return {
        "google": {"configured": get_google_oauth_config() is not None},
        "apple": {"configured": False},
    }


@router.get("/api/v1/auth/google/start")
def google_oauth_start(request: Request, next: str = Query("/")) -> Response:
    try:
        config = get_google_oauth_config(required=True)
        if config is None:
            raise GoogleOAuthError("Google sign-in is unavailable.", code="google_unavailable")
        transaction = issue_transaction("google", next)
        url = build_google_authorization_url(transaction.state, nonce=transaction.nonce, config=config)
        response = RedirectResponse(url=url, status_code=303)
        _set_oauth_state_cookie(response, request, transaction.state, provider="google")
        return response
    except Exception as exc:
        return _oauth_failure_redirect(request, "google", "google_unavailable", exc)


@router.get("/api/v1/auth/google/callback")
def google_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> Response:
    try:
        transaction = consume_transaction(
            str(state or ""),
            "google",
            expected_state=request.cookies.get(OAUTH_STATE_COOKIE),
        )
        if error:
            raise GoogleOAuthError("Google authorization was not completed.", code="oauth_cancelled")
        config = get_google_oauth_config(required=True)
        if config is None:
            raise GoogleOAuthError("Google sign-in is unavailable.", code="google_unavailable")
        profile = exchange_google_code(
            str(code or ""),
            transaction.state,
            config=config,
            expected_nonce=transaction.nonce,
        )
        ok, user, action = login_or_register_oauth_user(
            provider="google",
            name=str(profile.get("name") or "Google User"),
            email=str(profile.get("email") or ""),
            provider_subject=str(profile.get("google_id") or ""),
            email_verified=True,
        )
        if not ok or not isinstance(user, dict):
            raise GoogleOAuthError("Google account linking was refused.", code="google_account_linking_refused")
        target = transaction.next_path
        if action == "created":
            target = "/onboarding?" + urlencode({"next": transaction.next_path})
        if mfa_status(int(user["id"]))["enabled"]:
            challenge = issue_mfa_challenge(user, next_path=target)
            response = RedirectResponse(
                url="/login?" + urlencode({"next": target, "mfa": "required"}),
                status_code=303,
            )
            _set_mfa_challenge_cookie(response, request, challenge)
        else:
            response = RedirectResponse(url=target, status_code=303)
            _set_session_cookie(response, request, create_access_token(user, request=request))
        _clear_oauth_state_cookie(response, request, provider="google")
        return response
    except OAuthStateError as exc:
        return _oauth_failure_redirect(request, "google", "oauth_state_invalid", exc)
    except GoogleOAuthError as exc:
        code_name = str(getattr(exc, "code", "") or "").strip()
        if not code_name:
            text = str(exc).lower()
            if error:
                code_name = "oauth_cancelled"
            elif "refused" in text or "linking" in text:
                code_name = "oauth_link_refused"
            elif "expired" in text:
                code_name = "oauth_id_token_expired"
            elif "identity token" in text:
                code_name = "oauth_id_token_missing"
            elif "issuer or nonce" in text:
                code_name = "oauth_nonce_invalid"
            elif "validation failed" in text:
                code_name = "oauth_id_token_invalid"
            elif "not verified" in text:
                code_name = "oauth_email_unverified"
            elif "usable account identity" in text:
                code_name = "oauth_profile_incomplete"
            elif "authorization code" in text:
                code_name = "oauth_code_missing"
            else:
                code_name = "oauth_provider_unavailable"
        return _oauth_failure_redirect(request, "google", code_name, exc)
    except ValueError as exc:
        return _oauth_failure_redirect(request, "google", "oauth_identity_invalid", exc)
    except Exception as exc:
        return _oauth_failure_redirect(request, "google", "google_sign_in_failed", exc)


# =====================================================================
# Login anomaly visibility (new-device / impossible-travel)
# =====================================================================

@router.get("/api/v1/auth/login-anomalies")
def login_anomalies_endpoint(
    user: dict[str, Any] = Depends(current_user),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    from services.login_anomaly import anomalies_for_user
    return {"anomalies": anomalies_for_user(int(user["id"]), limit)}


@router.post("/api/v1/auth/login-anomalies/{anomaly_id}/ack")
def login_anomaly_ack_endpoint(
    anomaly_id: int,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, bool]:
    from services.login_anomaly import acknowledge_anomaly
    return {"acknowledged": acknowledge_anomaly(int(user["id"]), anomaly_id)}


