from datetime import datetime, timedelta, timezone

from services import login_anomaly as la
from database import get_connection


def test_travel_compares_previous_account_login_not_only_same_fingerprint(temp_db, monkeypatch):
    conn = get_connection()
    conn.execute("INSERT INTO users(id,name,email,password) VALUES(1,'Test','travel@example.test','x')")
    conn.commit()
    conn.close()
    coords = {"203.0.113.0/24": (19.076, 72.8777), "198.51.100.0/24": (40.7128, -74.006)}
    monkeypatch.setattr(la, "geolocate", coords.get)
    start = datetime.now(timezone.utc)
    la.record_login(1, "Chrome", "203.0.113.1", at=start)
    result = la.record_login(1, "Chrome", "198.51.100.1", at=start + timedelta(minutes=10))
    assert result["new_device"] is True
    assert result["impossible_travel"] is True


def test_compressed_ipv6_prefixes_are_canonical():
    assert la.ip_prefix("2001:db8::1") == la.ip_prefix("2001:0db8:0000:0:0:0:0:2")
    assert la.ip_prefix("999.1.2.3") == "unknown"


def test_stale_device_requires_new_confirmation(temp_db):
    conn = get_connection()
    conn.execute("INSERT INTO users(id,name,email,password) VALUES(1,'Test','stale@example.test','x')")
    conn.commit()
    conn.close()
    start = datetime.now(timezone.utc) - timedelta(days=la.NEW_DEVICE_WINDOW_DAYS + 1)
    la.record_login(1, "Chrome", "203.0.113.1", at=start)
    assert la.record_login(1, "Chrome", "203.0.113.1")["new_device"] is True
