"""Tests for login anomaly protection (new-device, impossible-travel)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import login_anomaly as la

USER_ID = 99001


@pytest.fixture(autouse=True)
def _clean():
    la._ensure_tables()
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM login_anomalies WHERE user_id=?", (USER_ID,))
        conn.execute("DELETE FROM login_devices WHERE user_id=?", (USER_ID,))
        conn.execute(
            "INSERT OR IGNORE INTO users(id,name,email,password) VALUES(?,?,?,?)",
            (USER_ID, "Anom", "anom@example.com", "x"),
        )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("DELETE FROM login_anomalies WHERE user_id=?", (USER_ID,))
        conn.execute("DELETE FROM login_devices WHERE user_id=?", (USER_ID,))
        conn.commit()
    finally:
        conn.close()


def test_first_login_flags_new_device():
    result = la.record_login(USER_ID, user_agent="Mozilla/5.0", ip="203.0.113.10")
    assert result["new_device"] is True
    assert result["impossible_travel"] is False
    assert any(a["type"] == "new_device" for a in result["anomalies"])


def test_second_login_same_device_not_flagged():
    la.record_login(USER_ID, user_agent="Mozilla/5.0", ip="203.0.113.11")
    result = la.record_login(USER_ID, user_agent="Mozilla/5.0", ip="203.0.113.12")
    assert result["new_device"] is False
    assert result["anomalies"] == []


def test_different_user_agent_is_new_device():
    la.record_login(USER_ID, user_agent="Chrome/120", ip="203.0.113.10")
    result = la.record_login(USER_ID, user_agent="curl/8.0", ip="203.0.113.10")
    assert result["new_device"] is True


def test_same_ip_different_slash24_is_new_device():
    la.record_login(USER_ID, user_agent="Chrome/120", ip="203.0.113.10")
    result = la.record_login(USER_ID, user_agent="Chrome/120", ip="198.51.100.10")
    assert result["new_device"] is True


def test_impossible_travel_detected_with_coordinates(monkeypatch):
    # Seed prior login in Mumbai with known coordinates and same device hash.
    coords = {"203.0.113.0/24": (19.0760, 72.8777),      # Mumbai
              "198.51.100.0/24": (40.7128, -74.0060)}     # New York
    monkeypatch.setattr(la, "geolocate", lambda prefix: coords.get(prefix))
    monkeypatch.setattr(la, "hash_device", lambda ua, ip: "same-device")

    from database import get_connection
    conn = get_connection()
    try:
        first_seen = datetime.now(timezone.utc) - timedelta(hours=2)
        conn.execute(
            """INSERT INTO login_devices
               (user_id, device_hash, device_label, ip_prefix, lat, lon,
                first_seen, last_seen, seen_count)
               VALUES (?,?,?,?,?,?,?, ?, 1)
               ON CONFLICT(user_id, device_hash) DO UPDATE SET
                 lat=excluded.lat, lon=excluded.lon, ip_prefix=excluded.ip_prefix,
                 last_seen=excluded.last_seen""",
            (USER_ID, "same-device", "Chrome/120", "203.0.113.0/24",
             19.0760, 72.8777, first_seen.isoformat(), first_seen.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()

    # 1 hour later, login from New York prefix -> ~12,900 km / 1 h.
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    result = la.record_login(
        USER_ID, user_agent="Chrome/120", ip="198.51.100.44", at=later,
    )
    assert result["new_device"] is False
    assert result["impossible_travel"] is True
    assert any(a["type"] == "impossible_travel" for a in result["anomalies"])


def test_no_impossible_travel_without_coordinates(monkeypatch):
    la.record_login(USER_ID, user_agent="Chrome/120", ip="203.0.113.10")
    monkeypatch.setattr(la, "hash_device", lambda ua, ip: "same-device")
    monkeypatch.setattr(la, "geolocate", lambda prefix: None)  # no resolver
    from database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE login_devices SET device_hash='same-device' WHERE user_id=?",
            (USER_ID,),
        )
        conn.commit()
    finally:
        conn.close()
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    result = la.record_login(USER_ID, user_agent="Chrome/120", ip="198.51.100.44", at=later)
    # Missing geolocation must never produce false positives.
    assert result["impossible_travel"] is False


def test_ip_prefix_formatting():
    assert la.ip_prefix("203.0.113.42") == "203.0.113.0/24"
    assert la.ip_prefix("2001:db8:1234:5678::1").endswith("/48")
    assert la.ip_prefix(None) == "unknown"


def test_haversine_reasonable():
    mumbai_ny = la.haversine_km((19.0760, 72.8777), (40.7128, -74.0060))
    assert 12000 < mumbai_ny < 14000  # ~12,900 km
    assert la.haversine_km((19.0, 72.0), (19.0, 72.0)) == pytest.approx(0.0)


def test_anomalies_list_and_ack():
    la.record_login(USER_ID, user_agent="NewAgent/1.0", ip="203.0.113.10")
    rows = la.anomalies_for_user(USER_ID)
    assert rows and rows[0]["type"] == "new_device"
    assert la.acknowledge_anomaly(USER_ID, rows[0]["id"]) is True
    assert la.acknowledge_anomaly(USER_ID, rows[0]["id"]) is False  # already acked


def test_record_login_never_raises(monkeypatch):
    # Force an internal failure -> must return neutral result, not raise.
    def boom(*a, **k):
        raise RuntimeError("db down")
    import services.login_anomaly as mod
    real_conn = mod.get_connection
    monkeypatch.setattr(mod, "get_connection", boom)
    result = la.record_login(USER_ID, user_agent="X", ip=None)
    assert result == {"new_device": False, "impossible_travel": False, "anomalies": []}
    monkeypatch.setattr(mod, "get_connection", real_conn)
