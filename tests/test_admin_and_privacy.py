"""Roles, administrator scope, and the non-prescriptive public forecast contract.

These tests encode the product's privacy rules as executable statements: a normal
user may never receive model internals, an administrator may never receive
another account's private rows, and a secret embedded in an exception may never
reach a response, an administrator API, or a report.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
import api.deps as api_deps
import database
from services import admin_registry, error_registry
from services.admin_registry import AdminConfigurationError
from services.forecast_presentation import present_forecast, public_report_payload

ADMIN_A = "admin.one@example.com"
ADMIN_B = "admin.two@example.com"
ADMIN_C = "admin.three@example.com"
NORMAL = "trader@example.com"
PASSWORD: str = "StrongPass9!x"

# Exact contract of the admin-only detail block: every key present for admins
# and absent from the public payload. Adding a key here is a reviewed change.
INTERNAL_KEYS = {
    "methods", "validation", "drift", "training", "multi_horizon", "explainability",
    "aci_state", "circuit_clip", "cqr", "fan_chart", "hierarchical_shrinkage",
    "ipo_peer_blend", "market_regime", "min_width_floor_applied", "mondrian_groups",
    "range_estimators", "regime_model_weights", "tier", "volatility_forecast",
    "volatility_scorecard",
}
INTERNAL_MARKERS = (
    "LinearRegression",
    "conformal",
    "quantile_regression",
    "winkler",
    "empirical_coverage",
    "naive_baseline",
    "supervised_rows",
    "GradientBoosting",
    "arima",
)


def _forecast_result() -> dict:
    return {
        "symbol": "RELIANCE",
        "forecast": {"low": 90.0, "median": 102.0, "high": 110.0, "confidence_level": 0.8,
                     "label": "80% interval", "currency": "INR", "direction": "bullish"},
        "current_price": 100.0,
        "methods": {"stacking": {"base_models": ["GradientBoostingRegressor"], "meta_learner": "LinearRegression"},
                    "conformal": {"type": "chronological split-conformal", "absolute_residual_quantile": 4.2},
                    "quantile_regression": {"families": ["GradientBoostingRegressor"]},
                    "arima_baseline": {"available": True}},
        "validation": {"empirical_coverage": 0.79, "nominal_coverage": 0.8, "winkler_score": 12.0,
                       "mae": 1.0, "rmse": 1.4, "samples": 120, "naive_baseline_mae": 1.4,
                       "beats_naive_baseline": True, "mae_improvement_vs_naive_pct": 28.5},
        "drift": {"status": "stable", "drift_detected": False},
        "training": {"training_window": "1y", "timeframe": "1D", "rows": 900, "supervised_rows": 820,
                     "split": {"train": 500, "calibration": 160, "test": 160}, "chronological": True},
        "generated_at": "2026-09-01T00:00:00+00:00",
    }


@pytest.fixture
def client(temp_db, monkeypatch):
    monkeypatch.setenv(admin_registry.ADMIN_EMAILS_VAR, f"{ADMIN_A},{ADMIN_B},{ADMIN_C}")
    error_registry.reset()
    test_client = TestClient(api_main.app)
    test_client.headers.update({"Origin": "http://localhost:3000"})
    return test_client


def _register(client: TestClient, email: str, name: str = "Person") -> dict:
    response = client.post("/api/v1/auth/register", json={"name": name, "email": email, "password": PASSWORD, "date_of_birth": "1985-06-15"})
    assert response.status_code == 200, response.text
    client.post("/api/v1/auth/logout")
    return response.json()


def _sign_in(client: TestClient, email: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _promote(client: TestClient, email: str) -> None:
    _register(client, email, name="Admin")
    admin_registry.bootstrap_admins()
    _sign_in(client, email)


# ---------------------------------------------------------------------------
# Allowlist and promotion
# ---------------------------------------------------------------------------
class TestAdminAllowlist:
    def test_at_most_three_identities_are_accepted(self):
        assert admin_registry.configured_admin_emails(f"{ADMIN_A},{ADMIN_B},{ADMIN_C}") == [ADMIN_A, ADMIN_B, ADMIN_C]

    def test_a_fourth_identity_fails_closed_instead_of_being_truncated(self):
        with pytest.raises(AdminConfigurationError):
            admin_registry.configured_admin_emails(f"{ADMIN_A},{ADMIN_B},{ADMIN_C},fourth@example.com")

    def test_duplicates_and_case_do_not_consume_a_slot(self):
        assert admin_registry.configured_admin_emails(f"{ADMIN_A.upper()}, {ADMIN_A} ;{ADMIN_B}") == [ADMIN_A, ADMIN_B]

    def test_a_non_email_entry_is_rejected(self):
        with pytest.raises(AdminConfigurationError):
            admin_registry.configured_admin_emails("not-an-email")

    def test_bootstrap_accepts_three_identities(self):
        assert admin_registry.configured_admin_emails(f"{ADMIN_A},{ADMIN_B},{ADMIN_C}") == [ADMIN_A, ADMIN_B, ADMIN_C]
        assert len(admin_registry.configured_admin_emails(f"{ADMIN_A},{ADMIN_B},{ADMIN_C}")) <= admin_registry.MAX_ADMINS

    def test_bootstrap_is_idempotent_and_demotes_removed_identities(self, temp_db):
        for email in (ADMIN_A, ADMIN_B, NORMAL):
            database.get_connection().close()
        conn = database.get_connection()
        for email in (ADMIN_A, ADMIN_B, NORMAL):
            conn.execute("INSERT INTO users(name, email, password) VALUES(?,?,?)", ("P", email, "x"))
        conn.commit()
        conn.close()

        first = admin_registry.bootstrap_admins([ADMIN_A, ADMIN_B])
        second = admin_registry.bootstrap_admins([ADMIN_A, ADMIN_B])
        assert sorted(first["promoted"]) == sorted(second["promoted"]) == [ADMIN_A, ADMIN_B]

        # Removing an identity from configuration must remove the privilege too.
        admin_registry.bootstrap_admins([ADMIN_A])
        conn = database.get_connection()
        roles = dict(conn.execute("SELECT LOWER(email), role FROM users").fetchall())
        conn.close()
        assert roles[ADMIN_A] == "admin"
        assert roles[ADMIN_B] == "user"
        assert roles[NORMAL] == "user"

    def test_an_unregistered_identity_is_pending_not_created(self, temp_db):
        result = admin_registry.bootstrap_admins([ADMIN_A])
        assert result["pending"] == [ADMIN_A]
        conn = database.get_connection()
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        conn.close()


# ---------------------------------------------------------------------------
# Registration and authorization
# ---------------------------------------------------------------------------
class TestRegistrationCannotEscalate:
    def test_registration_ignores_a_requested_role(self, client):
        response = client.post(
            "/api/v1/auth/register",
            json={"name": "Sneaky", "email": NORMAL, "password": PASSWORD, "date_of_birth": "1985-06-15", "role": "admin", "is_admin": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"].get("role", "user") == "user"
        me = client.get("/api/v1/auth/me")
        assert me.json()["user"]["role"] == "user"

    def test_an_allowlisted_email_is_not_privileged_until_bootstrap_runs(self, client):
        client.post("/api/v1/auth/register", json={"name": "Admin", "email": ADMIN_A, "password": PASSWORD, "date_of_birth": "1985-06-15"})
        assert client.get("/api/v1/admin/overview").status_code == 403
        admin_registry.bootstrap_admins()
        _sign_in(client, ADMIN_A)
        assert client.get("/api/v1/admin/overview").status_code == 200

    def test_a_stale_admin_row_without_configuration_is_refused(self, client, monkeypatch):
        _promote(client, ADMIN_A)
        assert client.get("/api/v1/admin/overview").status_code == 200
        # The row still says admin, but the identity is no longer configured.
        monkeypatch.setenv(admin_registry.ADMIN_EMAILS_VAR, ADMIN_B)
        assert client.get("/api/v1/admin/overview").status_code == 403


ADMIN_ROUTES = [
    ("get", "/api/v1/admin/overview"),
    ("get", "/api/v1/admin/models"),
    ("get", "/api/v1/admin/errors"),
    ("get", "/api/v1/admin/audit"),
    ("get", "/api/v1/admin/settings"),
]


class TestAdminAuthorization:
    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_signed_out_callers_get_401(self, client, method, path):
        assert getattr(client, method)(path).status_code == 401

    @pytest.mark.parametrize("method,path", ADMIN_ROUTES)
    def test_normal_users_get_403(self, client, method, path):
        _register(client, NORMAL)
        _sign_in(client, NORMAL)
        assert getattr(client, method)(path).status_code == 403

    @pytest.mark.parametrize("email", [ADMIN_A, ADMIN_B, ADMIN_C])
    def test_each_configured_administrator_is_admitted(self, client, email):
        _promote(client, email)
        for method, path in ADMIN_ROUTES:
            assert getattr(client, method)(path).status_code == 200, path

    def test_maintenance_routes_are_administrator_only(self, client):
        _register(client, NORMAL)
        _sign_in(client, NORMAL)
        assert client.post("/api/v1/market/instruments/refresh").status_code == 403
        assert client.post("/api/v1/market/calendar/refresh/2026").status_code == 403

    def test_a_denied_attempt_is_audited(self, client):
        _register(client, NORMAL)
        _sign_in(client, NORMAL)
        client.get("/api/v1/admin/overview")
        actions = [event["action"] for event in admin_registry.admin_audit_events(50)]
        assert "admin_access_denied" in actions


class TestAdminCannotReachPrivateData:
    def test_no_admin_route_accepts_a_user_identifier(self, client):
        _promote(client, ADMIN_A)
        paths = [path for path in client.get("/openapi.json").json()["paths"] if path.startswith("/api/v1/admin")]
        assert paths
        assert not [path for path in paths if "{user" in path or "{email" in path or "{account" in path]

    def test_admin_overview_exposes_counts_only(self, client):
        _register(client, NORMAL)
        _sign_in(client, NORMAL)
        client.post("/api/v1/watchlist", json={"symbol": "RELIANCE"})
        client.post("/api/v1/auth/logout")
        _promote(client, ADMIN_A)
        body = client.get("/api/v1/admin/overview").json()
        assert body["counts"]["users"] >= 1
        serialized = json.dumps(body)
        assert NORMAL not in serialized
        assert "password_hash" not in serialized.lower()
        assert PASSWORD not in serialized
        assert "RELIANCE" not in serialized

    def test_an_administrator_sees_only_their_own_portfolio(self, client):
        _register(client, NORMAL)
        _sign_in(client, NORMAL)
        client.post("/api/v1/portfolio/buy", json={"symbol": "RELIANCE", "shares": 2, "buy_price": 100})
        client.post("/api/v1/auth/logout")
        _promote(client, ADMIN_A)
        holdings = client.get("/api/v1/portfolio").json()
        assert all(row.get("symbol") != "RELIANCE" for row in holdings.get("holdings", holdings.get("items", [])))


# ---------------------------------------------------------------------------
# Bounded settings
# ---------------------------------------------------------------------------
class TestBoundedSettings:
    def test_a_value_outside_its_bound_is_rejected(self, client):
        _promote(client, ADMIN_A)
        response = client.put("/api/v1/admin/settings", json={"updates": {"quote_cache_seconds": 10_000}})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "setting_out_of_bounds"
        assert admin_registry.get_settings()["quote_cache_seconds"] == 15

    def test_an_unknown_key_cannot_be_written(self, client):
        _promote(client, ADMIN_A)
        response = client.put("/api/v1/admin/settings", json={"updates": {"jwt_secret": "x"}})
        assert response.status_code == 422
        assert "jwt_secret" not in json.dumps(admin_registry.get_settings())

    def test_a_batch_is_applied_atomically(self, client):
        _promote(client, ADMIN_A)
        response = client.put(
            "/api/v1/admin/settings",
            json={"updates": {"quote_cache_seconds": 30, "history_max_segments": 99}},
        )
        assert response.status_code == 422
        # The valid half of a rejected batch must not have been written.
        assert admin_registry.get_settings()["quote_cache_seconds"] == 15

    def test_an_accepted_change_persists_and_is_audited(self, client):
        _promote(client, ADMIN_A)
        response = client.put("/api/v1/admin/settings", json={"updates": {"quote_cache_seconds": 42}})
        assert response.status_code == 200
        assert admin_registry.get_settings()["quote_cache_seconds"] == 42
        entries = [event for event in admin_registry.admin_audit_events(50) if event["action"] == "settings_update"]
        assert entries and entries[0]["outcome"] == "ok"

    def test_maintenance_actions_are_allowlisted(self, client):
        _promote(client, ADMIN_A)
        response = client.post("/api/v1/admin/maintenance/rm-rf")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "maintenance_action_unknown"


# ---------------------------------------------------------------------------
# Public forecast contract
# ---------------------------------------------------------------------------
class TestPublicForecastContract:
    def test_the_public_payload_has_no_internals(self):
        payload = present_forecast(_forecast_result())
        assert not INTERNAL_KEYS & set(payload)
        serialized = json.dumps(payload)
        for marker in INTERNAL_MARKERS:
            assert marker.lower() not in serialized.lower(), marker

    def test_the_public_payload_has_no_imperative_recommendation(self):
        payload = present_forecast(_forecast_result())
        forbidden_fields = {"action", "side", "entry_price", "target_price", "stop_loss", "quantity", "amount"}
        assert not forbidden_fields & set(payload)
        assert all(str(payload.get(key, "")).upper() not in {"BUY", "SELL"} for key in payload)

    def test_the_range_is_ordered_and_labelled_neutrally(self):
        payload = present_forecast(_forecast_result())
        research = payload["research_range"]
        assert research["low"] <= research["median_reference"] <= research["high"]
        assert payload["title"] == "Research range"
        assert payload["model_label"] == "calibrated interval ensemble"

    def test_an_administrator_additionally_receives_the_detail_block(self):
        payload = present_forecast(_forecast_result(), is_admin=True)
        assert set(payload["admin_detail"]) == INTERNAL_KEYS

    def test_zones_are_withheld_when_calibration_is_poor(self):
        result = _forecast_result()
        result["validation"]["empirical_coverage"] = 0.35
        payload = present_forecast(result)
        assert payload["observation_zone"] is None
        assert payload["risk_zone"] is None
        assert payload["zones_unavailable_reason"]

    def test_zones_are_withheld_when_the_baseline_is_not_beaten(self):
        result = _forecast_result()
        result["validation"]["beats_naive_baseline"] = False
        assert present_forecast(result)["observation_zone"] is None

    def test_every_published_bound_comes_from_validated_output(self):
        result = _forecast_result()
        payload = present_forecast(result)
        allowed = {90.0, 100.0, 102.0, 110.0}
        for zone in (payload["observation_zone"], payload["risk_zone"]):
            assert zone is None or {zone["low"], zone["high"]} <= allowed
        for scenario in payload["scenarios"]:
            assert {scenario["low"], scenario["high"]} <= allowed

    def test_a_report_payload_drops_every_internal_block(self):
        assert not INTERNAL_KEYS & set(public_report_payload(_forecast_result()))

    def test_the_predict_endpoint_returns_the_public_payload(self, client, monkeypatch):
        monkeypatch.setattr(api_deps, "forecast_range", lambda *a, **k: _forecast_result())
        monkeypatch.setattr(api_deps, "_history", lambda *a, **k: ("RELIANCE", _frame()))
        body = client.get("/api/v1/predict/RELIANCE?training_window=1y").json()
        assert not INTERNAL_KEYS & set(body)
        assert body["research_range"]["low"] == 90.0

    def test_the_predict_endpoint_adds_detail_for_an_administrator(self, client, monkeypatch):
        monkeypatch.setattr(api_deps, "forecast_range", lambda *a, **k: _forecast_result())
        monkeypatch.setattr(api_deps, "_history", lambda *a, **k: ("RELIANCE", _frame()))
        _promote(client, ADMIN_A)
        body = client.get("/api/v1/predict/RELIANCE?training_window=1y").json()
        assert "admin_detail" in body


def _frame():
    import pandas as pd

    index = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    frame = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 100.0, "Volume": 10}, index=index)
    frame.attrs.update({"source": "test", "provider": "test", "is_stale": False, "context": {}})
    return frame


# ---------------------------------------------------------------------------
# Sanitized errors
# ---------------------------------------------------------------------------
SECRET: str = "sk-live-DO-NOT-LEAK-4242"


class TestSecretsNeverLeak:
    def test_a_secret_in_an_exception_never_reaches_the_response(self, client, monkeypatch, caplog):
        def explode(*_args, **_kwargs):
            raise RuntimeError(f"upstream rejected token {SECRET}")

        monkeypatch.setattr(api_deps, "forecast_range", explode)
        monkeypatch.setattr(api_deps, "_history", lambda *a, **k: ("RELIANCE", _frame()))
        response = client.get("/api/v1/predict/RELIANCE?training_window=1y")
        assert response.status_code == 503
        assert SECRET not in response.text
        detail = response.json()["detail"]
        assert detail["code"] == "forecast_unavailable"
        assert detail["retryable"] is True
        assert detail["support_id"]

    def test_the_administrator_error_view_holds_no_exception_text(self, client, monkeypatch):
        def explode(*_args, **_kwargs):
            raise RuntimeError(f"upstream rejected token {SECRET}")

        monkeypatch.setattr(api_deps, "forecast_range", explode)
        monkeypatch.setattr(api_deps, "_history", lambda *a, **k: ("RELIANCE", _frame()))
        client.get("/api/v1/predict/RELIANCE?training_window=1y")
        _promote(client, ADMIN_A)
        body = client.get("/api/v1/admin/errors").json()
        assert body["summary"]["total_failures"] >= 1
        assert SECRET not in json.dumps(body)
        assert body["groups"][0]["exception_type"] == "RuntimeError"

    def test_failures_differing_only_by_a_secret_group_together(self):
        error_registry.reset()
        for value in ("token-A", "token-B", "token-C"):
            try:
                raise RuntimeError(f"rejected {value}")
            except RuntimeError as exc:
                error_registry.record_error("forecast", "abc123", exc)
        groups = error_registry.error_groups()
        assert len(groups) == 1
        assert groups[0]["count"] == 3

    def test_the_audit_log_refuses_to_store_a_secret(self, temp_db):
        admin_registry.record_admin_action(
            actor_id=1, actor_email=ADMIN_A, action="settings_update",
            detail={"api_token": SECRET, "password": SECRET, "keys": "quote_cache_seconds"},
        )
        events = admin_registry.admin_audit_events(10)
        assert SECRET not in json.dumps(events)
        assert events[0]["detail"] == {"keys": "quote_cache_seconds"}


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
class TestRoleMigration:
    def test_the_role_column_is_additive_and_defaults_to_user(self, temp_db):
        conn = database.get_connection()
        conn.execute("INSERT INTO users(name, email, password) VALUES('Old','legacy@example.com','x')")
        conn.commit()
        assert conn.execute("SELECT role FROM users WHERE email='legacy@example.com'").fetchone()[0] == "user"
        conn.close()

    def test_running_the_migration_again_preserves_roles(self, temp_db):
        conn = database.get_connection()
        conn.execute("INSERT INTO users(name, email, password, role) VALUES('A',?, 'x','admin')", (ADMIN_A,))
        conn.commit()
        conn.close()
        database.create_tables()
        conn = database.get_connection()
        assert conn.execute("SELECT role FROM users WHERE email=?", (ADMIN_A,)).fetchone()[0] == "admin"
        conn.close()
