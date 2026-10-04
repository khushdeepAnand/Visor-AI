"""Read-only Security Center: live, observed checks against the running app.

Every check below derives its status from something the running API actually
did during this call (an HTTP probe through the real middleware stack, the
live route/dependency graph, or a direct call to the real validation code).
No result is hardcoded: if an observation cannot be made in this deployment,
the check says so explicitly instead of claiming PASS.
"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import date, timedelta
from typing import Any, Callable

from fastapi.testclient import TestClient

# Imports stay inside the function bodies so that importing this module from
# api/main.py never creates a circular import; the app object is only read
# lazily when an administrator opens the scorecard.
from authentication import MINIMUM_ACCOUNT_AGE, validate_date_of_birth


PROBE_EMAIL_DOMAIN = "stockpilot.local"
PROTECTED_PROBE_PATHS = (
    "/api/v1/auth/me",
    "/api/v1/watchlist",
    "/api/v1/portfolio",
    "/api/v1/strategies",
    "/api/v1/paper/account",
)
USER_OWNED_PATTERNS = (
    "/api/v1/auth/sessions",
    "/api/v1/auth/me",
    "/api/v1/forecast-jobs",
    "/api/v1/predictions/",
    "/api/v1/strategies/",
    "/api/v1/forward-tests",
    "/api/v1/watchlist",
    "/api/v1/portfolio",
    "/api/v1/alerts",
    "/api/v1/paper/account",
    "/api/v1/paper/orders",
    "/api/v1/paper/journal",
    "/api/v1/paper/badges",
    "/api/v1/paper/leaderboard/visibility",
    "/api/v1/paper/challenges/historical/mine",
    "/api/v1/screener/saved",
    "/api/v1/chart-layouts",
)
#: Routes that legitimately live under a user-owned pattern but are public by
#: design (leaderboards, challenge browse views) and must not fail the check.
PUBLIC_EXCLUSIONS = {
    "/api/v1/strategies/metadata",
    "/api/v1/alerts/evaluate",
    "/api/v1/paper/leaderboard",
    "/api/v1/paper/challenges",
    "/api/v1/paper/challenges/historical",
}
AUTH_DEPENDENCIES = {"current_user", "require_admin", "optional_user"}
FORBIDDEN_SESSION_KEYS = {
    "password",
    "password_hash",
    "date_of_birth",
    "token",
    "jti",
    "token_version",
}


def _probe_email() -> str:
    return f"security-probe-{secrets.token_hex(6)}@{PROBE_EMAIL_DOMAIN}"


def _cleanup_probe_user(email: str) -> bool:
    """Remove a probe account's rows so the scorecard never leaves users behind."""
    from database import get_connection

    try:
        connection = get_connection()
        row = connection.execute(
            "SELECT id FROM users WHERE LOWER(email)=? LIMIT 1",
            (str(email).strip().lower(),),
        ).fetchone()
        if row is None:
            connection.close()
            return True
        user_id = int(row[0])
        user_scoped_deletes = {
            "auth_sessions": "DELETE FROM auth_sessions WHERE user_id=?",
            "settings": "DELETE FROM settings WHERE user_id=?",
            "oauth_identities": "DELETE FROM oauth_identities WHERE user_id=?",
        }
        for statement in user_scoped_deletes.values():
            try:
                connection.execute(statement, (user_id,))
            except sqlite3.Error:
                pass
        try:
            connection.execute("DELETE FROM auth_login_attempts WHERE identifier=?", (str(email).strip().lower(),))
        except sqlite3.Error:
            pass
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))
        connection.commit()
        connection.close()
        return True
    except Exception:
        return False


def _dependency_names(route: Any) -> set[str]:
    """Return the runtime dependency function names FastAPI resolved for a route."""
    names: set[str] = set()

    def walk(dependant: Any) -> None:
        if dependant is None:
            return
        call = getattr(dependant, "call", None)
        if call is not None and not getattr(call, "__self__", None):
            names.add(getattr(call, "__name__", str(call)))
        for sub in getattr(dependant, "dependencies", []) or []:
            walk(sub)

    walk(getattr(route, "dependant", None))
    return names


def _routes(app: Any) -> list[Any]:
    return [route for route in getattr(app, "routes", []) if getattr(route, "path", "").startswith("/api/v1/")]


def _check_auth_enforcement(client: TestClient) -> tuple[str, str]:
    unguarded: list[str] = []
    for path in PROTECTED_PROBE_PATHS:
        response = client.get(path)
        if response.status_code != 401:
            unguarded.append(f"{path}->{response.status_code}")
    if unguarded:
        return "FAIL", "Anonymous access was accepted on: " + ", ".join(unguarded)
    return "PASS", "All sampled protected endpoints rejected anonymous requests with 401."


def _check_object_authorization(app: Any, client: TestClient) -> tuple[str, str]:
    unguarded: list[str] = []
    inspected = 0
    for route in _routes(app):
        path = getattr(route, "path", "")
        if not any(path.startswith(pattern) for pattern in USER_OWNED_PATTERNS):
            continue
        if path in PUBLIC_EXCLUSIONS:
            continue
        inspected += 1
        if not (AUTH_DEPENDENCIES & _dependency_names(route)):
            unguarded.append(path)
    enforced = client.get("/api/v1/strategies")
    probe_ok = enforced.status_code == 401
    if unguarded:
        return "FAIL", "Routes missing server-side session enforcement: " + ", ".join(unguarded)
    if not probe_ok:
        return "FAIL", "A user-owned route accepted an anonymous request."
    return (
        "PASS",
        f"{inspected} user-owned routes carry a server-side session dependency; anonymous probe returned 401.",
    )


def _check_session_security(
    client: TestClient,
    *,
    secure_cookie: bool,
) -> tuple[str, str, str | None]:
    email = _probe_email()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Security Probe",
            "email": email,
            "password": "ProbePass9",
            "date_of_birth": (date.today() - timedelta(days=15 * 366)).isoformat(),
        },
    )
    if response.status_code != 200:
        return (
            "WARNING",
            "Live cookie probe could not run (registration returned %d); no cookie flags were observed."
            % response.status_code,
            None,
        )
    cookie = response.headers.get("set-cookie", "")
    lowered = cookie.lower()
    observed = {
        "httponly": "httponly" in lowered,
        "samesite": "samesite=lax" in lowered,
        "secure": "secure" in lowered if secure_cookie else True,
    }
    passed = observed["httponly"] and observed["samesite"] and observed["secure"]
    if not passed:
        failed = [key for key, value in observed.items() if not value]
        return "FAIL", "Session cookie missing: " + ", ".join(failed) + " (flags observed).", email
    return (
        "PASS",
        "Session cookie observed as HttpOnly, SameSite=Lax%s on a live response."
        % ("" if secure_cookie else "(Secure omitted because the probe was plain HTTP)"),
        email,
    )


def _check_rate_limiting(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/v1/status")
    limit = response.headers.get("x-ratelimit-limit")
    window = response.headers.get("x-ratelimit-window")
    if response.status_code != 200 or limit is None or window is None:
        return "FAIL", "A live response carried no X-RateLimit headers."
    return "PASS", f"Live response reported X-RateLimit-Limit={limit} / window={window}s."


def _check_csrf(client: TestClient, cookie_name: str) -> tuple[str, str]:
    forged = "missing-or-invalid-session"
    missing_origin = client.post(
        "/api/v1/auth/logout", cookies={cookie_name: forged}
    )
    foreign_origin = client.post(
        "/api/v1/auth/logout", cookies={cookie_name: forged}, headers={"Origin": "https://evil.example"}
    )
    if missing_origin.status_code != 403 or foreign_origin.status_code != 403:
        return "FAIL", "A cookie-authenticated state change without/with a foreign Origin was not rejected."
    return "PASS", "Cookie-authenticated writes without an allowed Origin were rejected with 403."


def _check_cors(client: TestClient) -> tuple[str, str]:
    allowed = client.options(
        "/api/v1/status",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    foreign = client.options(
        "/api/v1/status",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allowed_origin = allowed.headers.get("access-control-allow-origin")
    if not allowed_origin:
        return (
            "WARNING",
            "No Access-Control-Allow-Origin observed on preflight; the CSRF origin gate remains the write boundary.",
        )
    if allowed_origin == "http://localhost:3000" and not foreign.headers.get("access-control-allow-origin"):
        return (
            "PASS",
            "Preflight echoed only the configured origin; foreign origin was excluded.",
        )
    return (
        "WARNING",
        "Preflight echoed an origin, but the allow/deny split could not be confirmed from two probes.",
    )


def _check_security_headers(client: TestClient) -> tuple[str, str]:
    required = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
        "cross-origin-opener-policy": "same-origin",
        "permissions-policy": None,
    }
    response = client.get("/api/v1/status")
    missing = [
        name if expected is None else f"{name}={expected}"
        for name, expected in required.items()
        if not str(response.headers.get(name) or "").lower().startswith((expected or "").lower())
    ]
    if missing:
        return "FAIL", "Missing security headers on a live response: " + ", ".join(missing)
    return "PASS", "Live response carried X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy and COOP."


def _find_sensitive_fields(payload: Any, path: str = "", found: set[str] | None = None) -> set[str]:
    found = found if found is not None else set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if lowered in FORBIDDEN_SESSION_KEYS:
                found.add(lowered)
            _find_sensitive_fields(value, f"{path}/{key}", found)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _find_sensitive_fields(item, f"{path}/{index}", found)
    return found


def _check_sensitive_data_exposure(client: TestClient) -> tuple[str, str]:
    response = client.get("/api/v1/auth/me")
    if response.status_code != 200:
        return "WARNING", "/auth/me probe did not return 200; payload could not be verified."
    try:
        payload = response.json()
    except ValueError:
        return "WARNING", "/auth/me returned a non-JSON response; payload could not be verified."
    leaked = sorted(_find_sensitive_fields(payload))
    if leaked:
        return "FAIL", "Sensitive fields appeared in a user payload: " + ", ".join(leaked)
    return "PASS", "/auth/me response exposed no password, hash, date-of-birth, or token material."


def _check_age_restriction(client: TestClient) -> tuple[str, str]:
    # A fresh client (no session cookie) plus an approved Origin isolates the
    # age gate from the CSRF boundary; both are verified by their own checks.
    from api.main import app as API_APP

    client = TestClient(API_APP, base_url="https://testserver", follow_redirects=False)
    today = date.today()
    try:
        birthday = date(today.year - MINIMUM_ACCOUNT_AGE, today.month, today.day)
    except ValueError:  # 29 February in a non-leap target year.
        birthday = date(today.year - MINIMUM_ACCOUNT_AGE, today.month, 28)
    valid_dob = birthday.isoformat()          # exactly 15 -> accepted.
    invalid_dob = (birthday + timedelta(days=1)).isoformat()  # 14y364d -> rejected.
    boundary_ok, _ = validate_date_of_birth(valid_dob, MINIMUM_ACCOUNT_AGE)
    boundary_rejected, _ = validate_date_of_birth(invalid_dob, MINIMUM_ACCOUNT_AGE)
    if not boundary_ok or boundary_rejected:
        return "FAIL", "The age-gate boundary logic itself rejected the 15th birthday or accepted 14y364d."
    email = _probe_email()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Age Probe",
            "email": email,
            "password": "ProbePass9",
            "date_of_birth": invalid_dob,
        },
        headers={"Origin": "http://localhost:3000"},
    )
    try:
        if response.status_code != 400 or "aged 15" not in response.text:
            return "FAIL", "A 14-year-old registration was not rejected by the live endpoint."
        return "PASS", "Exactly-15 accepted and 14y364d rejected by the real age gate and live endpoint."
    finally:
        _cleanup_probe_user(email)


def _check_input_validation(client: TestClient) -> tuple[str, str]:
    response = client.post("/api/v1/auth/register", json={"email": "broken-email"})
    if response.status_code not in {400, 422}:
        return "FAIL", "Malformed registration input was not cleanly rejected."
    body = response.text.lower()
    if "traceback" in body or "file \"" in body or "line, in" in body:
        return "FAIL", "Malformed input leak returned an internal stack trace."
    return "PASS", "Malformed input produced a structured validation response with no stack trace."


def run_security_checks() -> dict[str, Any]:
    """Run all live security checks and return the scorecard payload."""
    from api.main import COOKIE_NAME, app as API_APP

    from middleware.security import SecurityHeadersMiddleware

    # Mirror the schema migration the app lifespan applies at boot, so the
    # live probes behave the same on a pre-existing database that predates
    # the age-gate column. Idempotent: a current database is a no-op.
    from database import create_tables, get_connection

    connection = get_connection()
    user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    connection.close()
    if "date_of_birth" not in user_columns:
        create_tables()

    checks: list[dict[str, Any]] = []
    client = TestClient(API_APP, follow_redirects=False)
    probe = Callable[..., tuple[str, str]]
    samples: list[tuple[str, str, probe, tuple[Any, ...]]] = [
        ("auth_enforcement", "Auth enforcement", _check_auth_enforcement, (client,)),
        ("object_authorization", "Object-level authorization", _check_object_authorization, (API_APP, client)),
        ("rate_limiting", "Rate limiting", _check_rate_limiting, (client,)),
        ("cors", "CORS", _check_cors, (client,)),
        ("security_headers", "Security headers", _check_security_headers, (client,)),
        ("input_validation", "Input validation", _check_input_validation, (client,)),
        # CSRF runs last so its forged session cookie cannot affect later probes.
        ("csrf", "CSRF", _check_csrf, (client, COOKIE_NAME)),
    ]
    for key, category, fn, args in samples:
        try:
            status, reason = fn(*args)
        except Exception as exc:  # noqa: BLE001  -- the scorecard must never fail the request.
            status, reason = "FAIL", f"Check could not complete: {type(exc).__name__}."
        checks.append({"key": key, "category": category, "status": status, "reason": reason})

    # Session-security and sensitive-data checks share one throwaway account,
    # which is cleaned up only after both observations have been made.
    session_client = TestClient(API_APP, base_url="https://testserver", follow_redirects=False)
    session_probe_email: str | None = None
    try:
        status, reason, session_probe_email = _check_session_security(session_client, secure_cookie=True)
        checks.append({"key": "session_security", "category": "Session security", "status": status, "reason": reason})
        status, reason = _check_sensitive_data_exposure(session_client)
        checks.append({"key": "sensitive_data_exposure", "category": "Sensitive-data exposure", "status": status, "reason": reason})
    finally:
        if session_probe_email:
            _cleanup_probe_user(session_probe_email)

    status, reason = _check_age_restriction(session_client)
    checks.append({"key": "age_restriction", "category": "Age restriction", "status": status, "reason": reason})

    security_middleware_present = any(
        getattr(entry, "cls", None) is SecurityHeadersMiddleware
        for entry in API_APP.user_middleware
    )
    if not security_middleware_present:
        for existing in checks:
            if existing["key"] == "security_headers":
                existing["status"] = "FAIL"
                existing["reason"] += " The SecurityHeadersMiddleware is absent from the live middleware stack."

    from datetime import datetime, timezone

    return {
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Scorecard is derived from live probes of this running deployment; unverifiable checks are reported as such.",
    }