from __future__ import annotations

from fastapi.testclient import TestClient

import database
from api.main import app
from services.compliance import RESEARCH_ACKNOWLEDGMENT_VERSION


ORIGIN = "http://localhost:3000"


def _register(client: TestClient) -> int:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "Erase Me",
            "email": "erase@example.com",
            "password": "StrongPass9",
            "date_of_birth": "1985-06-15",
        },
    )
    assert response.status_code == 200, response.text
    return int(response.json()["user"]["id"])


def test_research_acknowledgment_is_versioned_and_audited(temp_db):
    client = TestClient(app)
    user_id = _register(client)
    before = client.get("/api/v1/auth/me")
    assert before.status_code == 200
    assert before.json()["user"]["research_acknowledgment_required"] is True

    stale = client.post(
        "/api/v1/auth/research-acknowledgment",
        json={"version": "obsolete", "accepted": True},
        headers={"Origin": ORIGIN},
    )
    assert stale.status_code == 409

    response = client.post(
        "/api/v1/auth/research-acknowledgment",
        json={"version": RESEARCH_ACKNOWLEDGMENT_VERSION, "accepted": True},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    events = database.get_audit_events(user_id)
    assert events[0]["action"] == "research_disclaimer_acknowledged"
    assert events[0]["entity_id"] == RESEARCH_ACKNOWLEDGMENT_VERSION
    after = client.get("/api/v1/auth/me")
    assert after.status_code == 200
    assert after.json()["user"]["research_acknowledgment_required"] is False


def test_account_deletion_purges_lazy_feature_tables_and_session(temp_db):
    client = TestClient(app)
    user_id = _register(client)
    connection = database.get_connection()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_definitions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS screener_saved_screens(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS saved_chart_layouts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS forward_tests(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS forward_test_events(id INTEGER PRIMARY KEY, forward_test_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_step_up_failures(id INTEGER PRIMARY KEY, actor_email TEXT NOT NULL);
            """
        )
        connection.execute("INSERT INTO strategy_definitions(id,user_id) VALUES(1,?)", (user_id,))
        connection.execute("INSERT INTO screener_saved_screens(id,user_id) VALUES(1,?)", (user_id,))
        connection.execute("INSERT INTO saved_chart_layouts(id,user_id) VALUES(1,?)", (user_id,))
        connection.execute("INSERT INTO forward_tests(id,user_id) VALUES(1,?)", (user_id,))
        connection.execute("INSERT INTO forward_test_events(id,forward_test_id) VALUES(1,1)")
        connection.execute("INSERT INTO admin_step_up_failures(id,actor_email) VALUES(1,'erase@example.com')")
        connection.execute(
            "INSERT INTO admin_audit_log(actor_user_id,actor_email,action,outcome) VALUES(?,?,?,?)",
            (user_id, "erase@example.com", "test", "ok"),
        )
        connection.commit()
    finally:
        connection.close()

    response = client.request(
        "DELETE",
        "/api/v1/auth/account",
        json={"confirmation": "DELETE"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}
    assert client.get("/api/v1/auth/me").status_code == 401

    connection = database.get_connection()
    try:
        for table in (
            "users",
            "auth_sessions",
            "strategy_definitions",
            "screener_saved_screens",
            "saved_chart_layouts",
            "forward_tests",
            "forward_test_events",
            "admin_step_up_failures",
            "admin_audit_log",
        ):
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == 0, table
    finally:
        connection.close()
