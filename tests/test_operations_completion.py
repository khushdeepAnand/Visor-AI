import json

import pytest
from pydantic import ValidationError

from api.routers import admin
from api.deps import AccountLifecyclePayload
from database import get_connection
from services import admin_registry, compliance
from services.retention import RetentionPolicy, enforce_database_retention


@pytest.fixture
def accounts(temp_db):
    conn = get_connection()
    for uid in (1, 2):
        conn.execute("INSERT INTO users(id,name,email,password) VALUES(?,?,?,'x')", (uid, f"User {uid}", f"u{uid}@example.test"))
    conn.execute("INSERT INTO user_mfa(user_id,encrypted_totp_secret,enabled) VALUES(1,'encrypted',1)")
    conn.commit()
    conn.close()


def test_directory_reads_actual_mfa_table(accounts):
    result = admin.user_directory_endpoint(search="", mfa=True, limit=50, user={}, min_age_days=None, anomalous=None)
    assert [row["id"] for row in result["items"]] == [1]


def test_lifecycle_requires_explicit_nonblank_justification():
    for fields in ({}, {"reason": " " * 12}):
        with pytest.raises(ValidationError):
            AccountLifecyclePayload(account_id=1, action="suspend", **fields)


def test_bulk_missing_account_rolls_back_everything(accounts):
    with pytest.raises(ValueError):
        admin_registry.apply_account_actions([1, 999], "suspend", actor_id=2, actor_email="u2@example.test", reason="Investigation requested")
    conn = get_connection()
    assert conn.execute("SELECT account_status FROM users WHERE id=1").fetchone()[0] == "active"
    assert conn.execute("SELECT COUNT(*) FROM admin_audit_log").fetchone()[0] == 0
    conn.close()


def test_bulk_mfa_reset_revokes_sessions_and_factors_atomically(accounts):
    from services.webauthn import _ensure_table
    _ensure_table()
    conn = get_connection()
    conn.execute("INSERT INTO auth_sessions(user_id,jti_hash,token_version,issued_at,expires_at) VALUES(1,'hash',0,'2026-01-01','2027-01-01')")
    conn.commit()
    conn.close()
    result = admin_registry.apply_account_actions([1, 1], "force_mfa_reset", actor_id=2, actor_email="u2@example.test", reason="Verified account recovery")
    assert result["user_ids"] == [1]
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM user_mfa WHERE user_id=1").fetchone()[0] == 0
    assert conn.execute("SELECT revoked_at FROM auth_sessions").fetchone()[0]
    assert conn.execute("SELECT token_version FROM users WHERE id=1").fetchone()[0] == 1
    detail = json.loads(conn.execute("SELECT detail FROM admin_audit_log").fetchone()[0])
    assert detail["reason"] == "Verified account recovery"
    conn.close()


def test_compliance_acknowledgment_survives_audit_retention(accounts):
    conn = get_connection()
    conn.execute("INSERT INTO audit_log(user_id,action,entity_id,created_at) VALUES(1,'research_disclaimer_acknowledged',?,'2020-01-01')", (compliance.RESEARCH_ACKNOWLEDGMENT_VERSION,))
    conn.commit()
    conn.close()
    enforce_database_retention(RetentionPolicy())
    assert compliance.research_acknowledgment_required(1) is False


def test_regulatory_checklist_is_persistent_and_audited(accounts):
    compliance.update_review_item("external_penetration_test", status="in_progress", note="Reviewer engagement requested", actor_id=2, actor_email="u2@example.test")
    item = next(i for i in compliance.review_checklist() if i["id"] == "external_penetration_test")
    assert item["status"] == "in_progress"
    with pytest.raises(ValueError):
        compliance.update_review_item("made_up", status="complete", note="Reviewer engagement requested", actor_id=2, actor_email="u2@example.test")
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM admin_audit_log WHERE action='compliance_review_update'").fetchone()[0] == 1
    conn.close()
