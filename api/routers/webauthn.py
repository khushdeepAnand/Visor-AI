"""Router for WebAuthn passkeys (passwordless login + 2nd MFA factor).

Split from api/routers/auth.py so each router stays within the size budget
enforced by tests/test_frontend_api_contract.py.
"""

from fastapi import APIRouter
import jwt
from api.deps import *  # noqa: F401,F403
from api.routers.auth import MFA_CHALLENGE_COOKIE, _clear_mfa_challenge_cookie
from services.auth_api import consume_mfa_challenge_for_passkey

router = APIRouter()


class WebAuthnPayload(BaseModel):
    """Browser WebAuthn result plus the single-use challenge echo."""
    challenge: str = Field(min_length=8, max_length=512)
    response: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, max_length=60)

    model_config = {"populate_by_name": True}


def _decode_mfa_challenge(challenge: str | None) -> int:
    """Validate the pending-login challenge cookie and return the user id."""
    if not challenge:
        raise HTTPException(status_code=401, detail="The verification challenge is invalid or expired.")
    import services.auth_api as auth_api
    try:
        payload = jwt.decode(
            challenge,
            auth_api._secret(),
            algorithms=[auth_api.JWT_ALGORITHM],
            issuer="stockpilot-ai",
            audience="stockpilot-mfa",
        )
        return int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=401, detail="The verification challenge is invalid or expired.") from exc


@router.get("/api/v1/auth/webauthn/credentials")
def webauthn_list_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    from services.webauthn import list_credentials, WEBAUTHN_AVAILABLE
    return {
        "available": WEBAUTHN_AVAILABLE,
        "credentials": list_credentials(int(user["id"])) if WEBAUTHN_AVAILABLE else [],
    }


@router.post("/api/v1/auth/webauthn/register/options")
def webauthn_register_options_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    from services.webauthn import begin_registration
    try:
        return begin_registration(user)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/auth/webauthn/register/verify")
def webauthn_register_verify_endpoint(
    payload: WebAuthnPayload,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    from services.webauthn import complete_registration
    try:
        return complete_registration(user, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/v1/auth/webauthn/credentials/{credential_pk}")
def webauthn_delete_endpoint(
    credential_pk: int,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, bool]:
    from services.webauthn import delete_credential
    return {"deleted": delete_credential(int(user["id"]), credential_pk)}


@router.post("/api/v1/auth/webauthn/mfa/options")
def webauthn_mfa_options_endpoint(
    challenge: str | None = Cookie(default=None, alias=MFA_CHALLENGE_COOKIE),
) -> dict[str, Any]:
    """Assertion options for completing an MFA login challenge with a passkey.

    The challenge cookie identifies the pending login (it carries sub=user id),
    so options are scoped to that user without revealing credentials.
    """
    user_id = _decode_mfa_challenge(challenge)
    from services.webauthn import begin_authentication
    try:
        return begin_authentication(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/v1/auth/webauthn/mfa/verify")
def webauthn_mfa_verify_endpoint(
    payload: WebAuthnPayload,
    response: Response,
    request: Request,
    challenge: str | None = Cookie(default=None, alias=MFA_CHALLENGE_COOKIE),
) -> dict[str, Any]:
    """Complete an MFA login challenge using a passkey assertion."""
    user_id = _decode_mfa_challenge(challenge)

    from services.webauthn import complete_authentication
    if challenge is None:
        raise HTTPException(status_code=401, detail="The verification challenge is invalid or expired.")
    try:
        complete_authentication(user_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        user_id, next_path = consume_mfa_challenge_for_passkey(challenge)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    from authentication import get_user_by_id
    user = get_user_by_id(user_id)
    if not user or user.get("account_status") != "active":
        raise HTTPException(status_code=401, detail="The verification challenge is invalid or expired.")
    _set_session_cookie(response, request, create_access_token(user, request=request))
    _clear_mfa_challenge_cookie(response, request)
    return {"user": user, "next": next_path, "method": "webauthn"}
