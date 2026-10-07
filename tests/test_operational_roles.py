import pytest
from fastapi import HTTPException

from api.routers import admin
from services import admin_registry


def user(role):
    return {"id": 1, "email": "operator@example.test", "role": role}


def test_model_ops_cannot_read_accounts_or_use_admin_actions(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_MODEL_OPS_EMAILS", "operator@example.test")
    assert admin.require_model_ops(user("model-ops"))["id"] == 1
    with pytest.raises(HTTPException) as error:
        admin.require_support_ops(user("model-ops"))
    assert error.value.status_code == 403
    with pytest.raises(HTTPException):
        admin.require_admin(user("model-ops"))


def test_support_ops_cannot_mutate_models(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_SUPPORT_OPS_EMAILS", "operator@example.test")
    assert admin.require_support_ops(user("support-ops"))["id"] == 1
    with pytest.raises(HTTPException):
        admin.require_model_ops(user("support-ops"))


def test_stale_role_or_email_alone_never_grants_access(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_MODEL_OPS_EMAILS", "operator@example.test")
    assert admin_registry.effective_role(user("user")) == "user"
    monkeypatch.delenv("STOCKPILOT_MODEL_OPS_EMAILS")
    assert admin_registry.effective_role(user("model-ops")) == "user"


def test_operational_bootstrap_preserves_admin_and_demotes_removed_ops(temp_db, monkeypatch):
    from authentication import register_user
    from database import get_connection
    assert register_user("Operator", "operator@example.test", "GoodPassword123!", "1990-01-01")[0]
    monkeypatch.setenv("STOCKPILOT_MODEL_OPS_EMAILS", "operator@example.test")
    admin_registry.bootstrap_operational_roles()
    conn = get_connection()
    assert conn.execute("SELECT role FROM users").fetchone()[0] == "model-ops"
    conn.close()
    monkeypatch.delenv("STOCKPILOT_MODEL_OPS_EMAILS")
    admin_registry.bootstrap_operational_roles()
    conn = get_connection()
    assert conn.execute("SELECT role FROM users").fetchone()[0] == "user"
    conn.close()
