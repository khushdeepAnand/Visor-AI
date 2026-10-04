from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import database
from api.scheduler import create_retention_scheduler
from services.retention import RetentionPolicy, run_retention_enforcement


def test_retention_purges_only_expired_or_out_of_policy_rows(temp_db, tmp_path):
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=800)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    expired = (now - timedelta(minutes=1)).isoformat()
    future = (now + timedelta(hours=1)).isoformat()
    connection = database.get_connection()
    user_id = connection.execute(
        "INSERT INTO users(name,email,password) VALUES(?,?,?)",
        ("Retention User", "retention@example.com", "hash"),
    ).lastrowid
    connection.executemany(
        "INSERT INTO auth_sessions(user_id,jti_hash,token_version,issued_at,expires_at,revoked_at) VALUES(?,?,?,?,?,?)",
        [
            (user_id, "expired", 0, old, expired, None),
            (user_id, "active", 0, recent, future, None),
        ],
    )
    connection.executemany(
        "INSERT INTO audit_log(user_id,action,created_at) VALUES(?,?,?)",
        [(user_id, "old", old), (user_id, "recent", recent)],
    )
    connection.executemany(
        "INSERT INTO admin_audit_log(actor_user_id,action,outcome,created_at) VALUES(?,?,?,?)",
        [(user_id, "old", "success", old), (user_id, "recent", "success", recent)],
    )
    connection.execute(
        "INSERT INTO password_reset_tokens(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
        (user_id, "expired-reset", expired, old),
    )
    connection.execute(
        "INSERT INTO mfa_challenges(user_id,jti_hash,expires_at,created_at) VALUES(?,?,?,?)",
        (user_id, "expired-challenge", expired, old),
    )
    connection.executemany(
        "INSERT INTO auth_login_attempts(identifier,failed_count,locked_until,updated_at) VALUES(?,?,?,?)",
        [
            ("stale@example.com", 2, None, old),
            ("locked@example.com", 5, future, old),
            ("recent@example.com", 1, None, recent),
        ],
    )
    connection.commit()
    connection.close()

    artifact_directory = tmp_path / "model_refresh"
    artifact_directory.mkdir()
    old_artifact = artifact_directory / "old.json"
    new_artifact = artifact_directory / "new.json"
    old_artifact.write_text("{}", encoding="utf-8")
    new_artifact.write_text("{}", encoding="utf-8")
    old_epoch = (now - timedelta(days=31)).timestamp()
    os.utime(old_artifact, (old_epoch, old_epoch))
    recent_epoch = (now - timedelta(days=2)).timestamp()
    os.utime(new_artifact, (recent_epoch, recent_epoch))

    result = run_retention_enforcement(
        RetentionPolicy(
            audit_days=365,
            admin_audit_days=730,
            security_artifact_days=7,
            forecast_artifact_days=30,
            batch_size=100,
        ),
        artifact_directory=artifact_directory,
        now=now,
    )

    assert result["counts"]["auth_sessions"] == 1
    assert result["counts"]["audit_log"] == 1
    assert result["counts"]["admin_audit_log"] == 1
    assert result["counts"]["password_reset_tokens"] == 1
    assert result["counts"]["mfa_challenges"] == 1
    assert result["counts"]["auth_login_attempts"] == 1
    assert result["counts"]["forecast_artifacts"] == 1
    assert not old_artifact.exists()
    assert new_artifact.exists()

    connection = database.get_connection()
    assert connection.execute("SELECT jti_hash FROM auth_sessions").fetchall() == [("active",)]
    assert connection.execute("SELECT action FROM audit_log").fetchall() == [("recent",)]
    assert connection.execute("SELECT action FROM admin_audit_log").fetchall() == [("recent",)]
    assert connection.execute("SELECT identifier FROM auth_login_attempts ORDER BY identifier").fetchall() == [
        ("locked@example.com",),
        ("recent@example.com",),
    ]
    connection.close()


def test_retention_scheduler_is_daily_and_non_overlapping(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_RETENTION_HOUR", "3")
    monkeypatch.setenv("STOCKPILOT_RETENTION_MINUTE", "25")
    scheduler = create_retention_scheduler()
    job = scheduler.get_job("stockpilot-retention-enforcement")
    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert "hour='3'" in str(job.trigger)
    assert "minute='25'" in str(job.trigger)
