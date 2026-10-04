"""Configuration doctor: explain exactly why a capability is not working.

The most common "the app is broken" report on this project is not a bug: it is
an unset environment variable. Google sign-in raises
`GoogleOAuthConfigurationError` and the Upstox provider refuses to serve, both
correctly, but the operator is left guessing which value is missing.

This module turns that guessing into a checklist. Every check returns:

- `status`: `ok`, `action_required`, or `degraded`
- `missing`: the exact environment variable names that are unset
- `detail`: what breaks while it stays unset
- `fix`: the concrete next step, including the redirect URI that must match

Secrets are never returned. Only presence, length class, and derived metadata
(such as token expiry, which is read from the token's own unverified payload)
are reported.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

try:  # pragma: no cover - exercised implicitly in both environments
    from services.market_data.upstox_auth import decode_token_metadata, expiry_warning
except Exception:  # noqa: BLE001 - the doctor must never fail to load
    decode_token_metadata = None  # type: ignore[assignment]

STATUS_OK = "ok"
STATUS_ACTION = "action_required"
STATUS_DEGRADED = "degraded"

#: Checks are ordered so that the first `action_required` is also the first
#: thing an operator should fix.
CHECK_ORDER: tuple[str, ...] = (
    "core",
    "session",
    "database",
    "google_sign_in",
    "upstox_credentials",
    "upstox_token",
    "market_data_fallback",
    "frontend_origin",
)

SECRET_KEYS: tuple[str, ...] = (
    "STOCKPILOT_JWT_SECRET",
    "GOOGLE_CLIENT_SECRET",
    "UPSTOX_API_SECRET",
    "UPSTOX_ACCESS_TOKEN",
    "UPSTOX_ANALYTICS_TOKEN",
)


def _values(environment: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environment is None else environment


def _present(values: Mapping[str, str], key: str) -> bool:
    return bool(str(values.get(key, "") or "").strip())


def _missing(values: Mapping[str, str], keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if not _present(values, key)]


def _check(
    name: str,
    *,
    status: str,
    summary: str,
    detail: str,
    fix: str | None = None,
    missing: list[str] | None = None,
    facts: dict[str, Any] | None = None,
    blocks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "detail": detail,
        "fix": fix,
        "missing": missing or [],
        "facts": facts or {},
        "blocks": blocks or [],
    }


# --- individual checks -----------------------------------------------------


def check_core(values: Mapping[str, str]) -> dict[str, Any]:
    environment = str(values.get("STOCKPILOT_ENV", "") or "").strip() or "development"
    return _check(
        "core",
        status=STATUS_OK,
        summary="Running in the %s environment." % environment,
        detail="STOCKPILOT_ENV selects config defaults and safety behaviour.",
        facts={"environment": environment},
    )


def check_session(values: Mapping[str, str]) -> dict[str, Any]:
    missing = _missing(values, ("STOCKPILOT_JWT_SECRET",))
    if missing:
        return _check(
            "session",
            status=STATUS_ACTION,
            summary="Session signing secret is not set.",
            detail=(
                "Without STOCKPILOT_JWT_SECRET the API cannot issue or verify a session, "
                "so every sign-in appears to succeed and then immediately logs you out."
            ),
            fix="Set STOCKPILOT_JWT_SECRET in .env to a random string of at least 32 characters.",
            missing=missing,
            blocks=["sign_in", "any_authenticated_request"],
        )
    secret = str(values.get("STOCKPILOT_JWT_SECRET", ""))
    weak = len(secret) < 32
    return _check(
        "session",
        status=STATUS_DEGRADED if weak else STATUS_OK,
        summary="Session secret is set but short." if weak else "Session secret is set.",
        detail=(
            "A secret under 32 characters is guessable; rotate it before exposing the app beyond localhost."
            if weak
            else "Sessions can be signed and verified."
        ),
        fix="Replace STOCKPILOT_JWT_SECRET with at least 32 random characters." if weak else None,
        facts={"secret_length": len(secret)},
    )


def check_database(values: Mapping[str, str]) -> dict[str, Any]:
    configured = _present(values, "DATABASE_URL")
    return _check(
        "database",
        status=STATUS_OK,
        summary="Using the configured DATABASE_URL." if configured else "Using the local SQLite default.",
        detail=(
            "DATABASE_URL is set, so the app will not fall back to the bundled SQLite file."
            if configured
            else "No DATABASE_URL is set. The app uses its local SQLite database, which is fine for single-machine use."
        ),
        facts={"configured": configured},
    )


def check_google_sign_in(values: Mapping[str, str]) -> dict[str, Any]:
    keys = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")
    missing = _missing(values, keys)
    redirect = str(values.get("GOOGLE_REDIRECT_URI", "") or "").strip()
    if missing:
        return _check(
            "google_sign_in",
            status=STATUS_ACTION,
            summary="Google sign-in is not configured.",
            detail=(
                "services/google_oauth.py raises 'Google sign-in is not configured' when any of these is blank, "
                "which is what a failing Google button looks like from the browser."
            ),
            fix=(
                "In Google Cloud Console create an OAuth 2.0 Web client, then set "
                + ", ".join(missing)
                + ". The Authorised redirect URI in Google must match GOOGLE_REDIRECT_URI byte for byte, "
                "including scheme, host, port and trailing path."
            ),
            missing=missing,
            blocks=["google_sign_in"],
        )

    warnings: list[str] = []
    if not redirect.startswith(("http://", "https://")):
        warnings.append("GOOGLE_REDIRECT_URI does not start with http:// or https://")
    if "localhost" in redirect:
        warnings.append(
            "GOOGLE_REDIRECT_URI uses 'localhost'. Google treats localhost and 127.0.0.1 as different "
            "origins, and the rest of this project's defaults use 127.0.0.1."
        )
    if redirect.endswith("/"):
        warnings.append("GOOGLE_REDIRECT_URI has a trailing slash, which Google will not match.")
    if not redirect.rstrip("/").endswith("/auth/google/callback"):
        warnings.append(
            "GOOGLE_REDIRECT_URI does not end with /auth/google/callback, which is the route this API serves."
        )
    return _check(
        "google_sign_in",
        status=STATUS_DEGRADED if warnings else STATUS_OK,
        summary="Google credentials are set, with warnings." if warnings else "Google sign-in is configured.",
        detail=" ".join(warnings) if warnings else "Client id, secret and redirect URI are all present.",
        fix=(
            "Align GOOGLE_REDIRECT_URI with the Authorised redirect URI in the Google Cloud Console client."
            if warnings
            else None
        ),
        facts={"redirect_uri": redirect, "warnings": warnings},
    )


def check_upstox_credentials(values: Mapping[str, str]) -> dict[str, Any]:
    keys = ("UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI")
    missing = _missing(values, keys)
    if missing:
        return _check(
            "upstox_credentials",
            status=STATUS_ACTION,
            summary="Upstox app credentials are incomplete.",
            detail=(
                "Without these the Upstox provider cannot complete its authorisation flow, so live and "
                "historical market data requests are refused before any network call is made."
            ),
            fix=(
                "Create an app at https://account.upstox.com/developer/apps, then set "
                + ", ".join(missing)
                + ". The app's redirect URI must equal UPSTOX_REDIRECT_URI exactly."
            ),
            missing=missing,
            blocks=["upstox_historical_data", "upstox_live_quotes"],
        )
    return _check(
        "upstox_credentials",
        status=STATUS_OK,
        summary="Upstox app credentials are set.",
        detail="API key, secret and redirect URI are present.",
        facts={"redirect_uri": str(values.get("UPSTOX_REDIRECT_URI", "") or "")},
    )


def check_upstox_token(values: Mapping[str, str]) -> dict[str, Any]:
    token = str(values.get("UPSTOX_ACCESS_TOKEN", "") or "").strip()
    if not token:
        return _check(
            "upstox_token",
            status=STATUS_ACTION,
            summary="No Upstox access token is present.",
            detail=(
                "Upstox access tokens are short lived and are not issued by the app credentials alone. "
                "Until one is present, every data request fails with provider_not_configured."
            ),
            fix="Run configure_upstox.ps1 (or the documented token exchange) and set UPSTOX_ACCESS_TOKEN.",
            missing=["UPSTOX_ACCESS_TOKEN"],
            blocks=["upstox_historical_data", "upstox_live_quotes"],
        )

    facts: dict[str, Any] = {"configured": True}
    if decode_token_metadata is not None:
        try:
            warning = expiry_warning(token)
            facts["expires_at"] = warning.expires_at
            facts["is_expired"] = warning.status == "expired"
            facts["expiry_status"] = warning.status
            facts["expires_in_seconds"] = warning.seconds_remaining
            facts["warn_before_seconds"] = warning.warn_before_seconds
        except Exception:  # noqa: BLE001 - unreadable metadata is not fatal
            facts["expires_at"] = None
            facts["is_expired"] = None
            facts["expiry_status"] = None
            facts["expires_in_seconds"] = None
            facts["warn_before_seconds"] = None

    if facts.get("is_expired") is True:
        return _check(
            "upstox_token",
            status=STATUS_ACTION,
            summary="The Upstox access token has expired.",
            detail=(
                "The token's own payload says it expired at %s. Requests will fail with "
                "provider_auth_expired until it is replaced." % facts.get("expires_at")
            ),
            fix="Re-run the Upstox authorisation and replace UPSTOX_ACCESS_TOKEN with the new token.",
            facts=facts,
            blocks=["upstox_historical_data", "upstox_live_quotes"],
        )
    if facts.get("expiry_status") == "expiring_soon":
        return _check(
            "upstox_token",
            status=STATUS_DEGRADED,
            summary="The Upstox access token expires soon.",
            detail=(
                "The token's own payload says it expires at %s (%s seconds remaining). "
                "Reauthorise before then so market data does not silently stop."
                % (facts.get("expires_at"), facts.get("expires_in_seconds"))
            ),
            fix="Re-run the Upstox authorisation and replace UPSTOX_ACCESS_TOKEN with a fresh token.",
            facts=facts,
        )
    return _check(
        "upstox_token",
        status=STATUS_OK,
        summary="An Upstox access token is present.",
        detail=(
            "Expiry metadata could not be read from the token, so validity is unknown until the first request."
            if facts.get("expires_at") is None
            else "Token expires at %s." % facts.get("expires_at")
        ),
        facts=facts,
    )


def check_market_data_fallback(values: Mapping[str, str]) -> dict[str, Any]:
    providers = {
        "upstox": _present(values, "UPSTOX_ACCESS_TOKEN") or _present(values, "UPSTOX_ANALYTICS_TOKEN"),
        "truedata": _present(values, "TRUEDATA_GATEWAY_URL") and _present(values, "TRUEDATA_GATEWAY_TOKEN"),
        "globaldatafeeds": _present(values, "GLOBALDATAFEEDS_GATEWAY_URL") and _present(values, "GLOBALDATAFEEDS_GATEWAY_TOKEN"),
    }
    configured = [name for name, ready in providers.items() if ready]
    if configured:
        return _check(
            "market_data_fallback",
            status=STATUS_OK,
            summary="%d market data provider(s) configured." % len(configured),
            detail="Configured live feeds: " + ", ".join(sorted(configured)) + ". Public fallbacks are mode-dependent.",
            facts={"providers": providers},
        )
    return _check(
        "market_data_fallback",
        status=STATUS_ACTION,
        summary="No market data provider is configured.",
        detail=(
            "No credentialed live feed is configured. Public yfinance/NSE fallbacks are mode-dependent "
            "and demo data requires explicit OFFLINE_DEMO mode."
        ),
        fix="Configure Upstox or a licensed normalized gateway, or explicitly select a documented fallback/demo mode.",
        facts={"providers": providers},
        blocks=["quotes", "candles", "screener", "forecasts"],
    )


def check_frontend_origin(values: Mapping[str, str]) -> dict[str, Any]:
    api_base = str(values.get("NEXT_PUBLIC_API_BASE", "") or "").strip()
    if not api_base:
        return _check(
            "frontend_origin",
            status=STATUS_OK,
            summary="Frontend uses same-origin API calls.",
            detail="An empty NEXT_PUBLIC_API_BASE keeps the session cookie attached and avoids CORS entirely.",
            facts={"api_base": ""},
        )
    return _check(
        "frontend_origin",
        status=STATUS_DEGRADED,
        summary="Frontend points at an explicit API origin.",
        detail=(
            "NEXT_PUBLIC_API_BASE is set to %s. Cross-origin requests drop the session cookie unless "
            "CORS and cookie flags are configured for that exact origin, which presents as being "
            "signed out immediately after signing in." % api_base
        ),
        fix="Leave NEXT_PUBLIC_API_BASE empty unless you are deliberately serving the API from another origin.",
        facts={"api_base": api_base},
    )


CHECKS = {
    "core": check_core,
    "session": check_session,
    "database": check_database,
    "google_sign_in": check_google_sign_in,
    "upstox_credentials": check_upstox_credentials,
    "upstox_token": check_upstox_token,
    "market_data_fallback": check_market_data_fallback,
    "frontend_origin": check_frontend_origin,
}


def run_diagnostics(environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Run every check and summarise what an operator must do next."""
    values = _values(environment)
    checks = [CHECKS[name](values) for name in CHECK_ORDER]
    action_required = [check for check in checks if check["status"] == STATUS_ACTION]
    degraded = [check for check in checks if check["status"] == STATUS_DEGRADED]

    blocked: list[str] = []
    for check in action_required:
        for capability in check["blocks"]:
            if capability not in blocked:
                blocked.append(capability)

    if action_required:
        overall = STATUS_ACTION
    elif degraded:
        overall = STATUS_DEGRADED
    else:
        overall = STATUS_OK

    return {
        "status": overall,
        "checks": checks,
        "action_required": [check["name"] for check in action_required],
        "degraded": [check["name"] for check in degraded],
        "blocked_capabilities": blocked,
        "next_step": action_required[0]["fix"] if action_required else (degraded[0]["fix"] if degraded else None),
        "secrets_returned": False,
    }


def missing_variables(environment: Mapping[str, str] | None = None) -> list[str]:
    """Flat list of every environment variable a check reported as missing."""
    seen: list[str] = []
    for check in run_diagnostics(environment)["checks"]:
        for key in check["missing"]:
            if key not in seen:
                seen.append(key)
    return seen


def render_text_report(environment: Mapping[str, str] | None = None) -> str:
    """Human-readable report for the command line."""
    report = run_diagnostics(environment)
    symbols = {STATUS_OK: "[ ok ]", STATUS_DEGRADED: "[warn]", STATUS_ACTION: "[FAIL]"}
    lines = ["StockPilot setup doctor", "=" * 60]
    for check in report["checks"]:
        lines.append("%s %-22s %s" % (symbols[check["status"]], check["name"], check["summary"]))
        if check["status"] != STATUS_OK:
            lines.append("       %s" % check["detail"])
            if check["missing"]:
                lines.append("       missing: %s" % ", ".join(check["missing"]))
            if check["fix"]:
                lines.append("       fix: %s" % check["fix"])
    lines.append("=" * 60)
    lines.append("overall: %s" % report["status"])
    if report["blocked_capabilities"]:
        lines.append("blocked: %s" % ", ".join(report["blocked_capabilities"]))
    if report["next_step"]:
        lines.append("next: %s" % report["next_step"])
    return "\n".join(lines)
