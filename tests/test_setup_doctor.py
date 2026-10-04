"""Tests for the configuration doctor."""

from __future__ import annotations

from services.setup_doctor import (
    STATUS_ACTION,
    STATUS_DEGRADED,
    STATUS_OK,
    missing_variables,
    render_text_report,
    run_diagnostics,
)

COMPLETE = {
    "STOCKPILOT_ENV": "development",
    "STOCKPILOT_JWT_SECRET": "x" * 48,
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "GOOGLE_REDIRECT_URI": "http://127.0.0.1:3000/api/v1/auth/google/callback",
    "UPSTOX_API_KEY": "key",
    "UPSTOX_API_SECRET": "secret",
    "UPSTOX_REDIRECT_URI": "http://127.0.0.1:3000/auth/upstox/callback",
    "UPSTOX_ACCESS_TOKEN": "opaque-token",
    "NEXT_PUBLIC_API_BASE": "",
}


def _check(report: dict, name: str) -> dict:
    return next(check for check in report["checks"] if check["name"] == name)


def test_empty_environment_names_every_missing_variable():
    report = run_diagnostics({})
    assert report["status"] == STATUS_ACTION
    missing = missing_variables({})
    for key in (
        "STOCKPILOT_JWT_SECRET",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "UPSTOX_API_KEY",
        "UPSTOX_API_SECRET",
        "UPSTOX_REDIRECT_URI",
        "UPSTOX_ACCESS_TOKEN",
    ):
        assert key in missing


def test_complete_environment_reports_ok():
    report = run_diagnostics(COMPLETE)
    assert report["status"] == STATUS_OK
    assert report["action_required"] == []
    assert report["blocked_capabilities"] == []
    assert report["next_step"] is None


def test_google_check_explains_what_breaks_and_how_to_fix_it():
    report = run_diagnostics({})
    google = _check(report, "google_sign_in")
    assert google["status"] == STATUS_ACTION
    assert google["missing"] == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"]
    assert "google_sign_in" in google["blocks"]
    assert "redirect" in google["fix"].lower()


def test_localhost_redirect_is_flagged_without_blocking():
    values = dict(COMPLETE, GOOGLE_REDIRECT_URI="http://localhost:3000/api/v1/auth/google/callback")
    google = _check(run_diagnostics(values), "google_sign_in")
    assert google["status"] == STATUS_DEGRADED
    assert any("localhost" in warning for warning in google["facts"]["warnings"])


def test_trailing_slash_and_wrong_path_are_flagged():
    values = dict(COMPLETE, GOOGLE_REDIRECT_URI="http://127.0.0.1:3000/oauth/")
    google = _check(run_diagnostics(values), "google_sign_in")
    assert google["status"] == STATUS_DEGRADED
    assert len(google["facts"]["warnings"]) >= 2


def test_missing_upstox_token_blocks_market_data():
    values = dict(COMPLETE)
    values["UPSTOX_ACCESS_TOKEN"] = ""
    report = run_diagnostics(values)
    token = _check(report, "upstox_token")
    assert token["status"] == STATUS_ACTION
    assert token["missing"] == ["UPSTOX_ACCESS_TOKEN"]
    assert "upstox_historical_data" in report["blocked_capabilities"]


def test_partial_upstox_credentials_name_only_the_missing_one():
    values = dict(COMPLETE)
    values["UPSTOX_API_SECRET"] = ""
    credentials = _check(run_diagnostics(values), "upstox_credentials")
    assert credentials["missing"] == ["UPSTOX_API_SECRET"]


def test_short_session_secret_is_degraded_not_blocking():
    values = dict(COMPLETE, STOCKPILOT_JWT_SECRET="short")
    report = run_diagnostics(values)
    session = _check(report, "session")
    assert session["status"] == STATUS_DEGRADED
    assert report["status"] == STATUS_DEGRADED


def test_cross_origin_api_base_is_explained():
    values = dict(COMPLETE, NEXT_PUBLIC_API_BASE="http://127.0.0.1:8000")
    frontend = _check(run_diagnostics(values), "frontend_origin")
    assert frontend["status"] == STATUS_DEGRADED
    assert "cookie" in frontend["detail"].lower()


def test_no_provider_configured_blocks_price_surfaces():
    values = {key: value for key, value in COMPLETE.items() if not key.startswith("UPSTOX")}
    report = run_diagnostics(values)
    fallback = _check(report, "market_data_fallback")
    assert fallback["status"] == STATUS_ACTION
    assert "screener" in fallback["blocks"]


def test_report_never_returns_secret_values():
    report = run_diagnostics(COMPLETE)
    serialised = repr(report)
    assert report["secrets_returned"] is False
    assert "client-secret" not in serialised
    assert "opaque-token" not in serialised
    assert "x" * 48 not in serialised


def test_text_report_lists_failures_first():
    text = render_text_report({})
    assert "StockPilot setup doctor" in text
    assert "[FAIL]" in text
    assert "overall: action_required" in text
    assert "next:" in text
