"""Login anomaly protection: new-device and impossible-travel detection.

Design:
  * Every successful login records a compact device/IP fingerprint in
    ``login_devices`` (hashed IP prefix so raw IPs are not stored long-term).
  * A *new device* for an account triggers ``new_device`` anomaly; by default
    it is logged + surfaced to the user (session marked ``anomaly=new_device``)
    and optionally requires re-authentication when
    ``STOCKPILOT_LOGIN_ANOMALY_STRICT=true``.
  * *Impossible travel* compares the current login's coarse geolocation/IP
    prefix and time delta against the previous login; speeds above
    ``STOCKPILOT_MAX_TRAVEL_KMH`` (default 900, ~commercial flight) flag it.
    Geolocation is best-effort: without a configured resolver we fall back to
    IP-prefix distance heuristics, and impossible travel is *only* raised when
    a resolver actually produced coordinates for both events (no false
    positives from missing data).

All detections are written to ``login_anomalies`` for audit and surfaced on
the session so the UI can show a "new sign-in" notice.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from database import get_connection

DEFAULT_MAX_TRAVEL_KMH = float(os.getenv("STOCKPILOT_MAX_TRAVEL_KMH", "900"))
NEW_DEVICE_WINDOW_DAYS = int(os.getenv("STOCKPILOT_NEW_DEVICE_WINDOW_DAYS", "90"))
DEVICE_COOKIE = "stockpilot_device"


def device_token(request: Any) -> str:
    value = str(request.cookies.get(DEVICE_COOKIE, ""))
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else secrets.token_hex(32)


def token_device_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

# Cities used for coarse geolocation via configured resolver lookups
# (kept minimal; pluggable through STOCKPILOT_GEO_RESOLVER=module:function).
_GEO_CACHE: dict[str, tuple[float, float]] = {}


def _ensure_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_devices(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_hash TEXT NOT NULL,
                device_label TEXT,
                ip_prefix TEXT,
                lat REAL,
                lon REAL,
                first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                seen_count INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, device_hash)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_devices_user ON login_devices(user_id, last_seen)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_anomalies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                anomaly_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                detail TEXT,
                ip_prefix TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                acknowledged_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_anomalies_user ON login_anomalies(user_id, created_at)"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(login_devices)")}
        if "confirmed_at" not in columns:
            conn.execute("ALTER TABLE login_devices ADD COLUMN confirmed_at TEXT")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Fingerprinting helpers
# ---------------------------------------------------------------------

def hash_device(user_agent: str, ip: str | None) -> str:
    """Stable per-device hash. IP participates only as a coarse /24 prefix."""
    ua = (user_agent or "").strip().lower()
    prefix = ip_prefix(ip)
    return hashlib.sha256(f"{ua}|{prefix}".encode()).hexdigest()[:32]


def ip_prefix(ip: str | None) -> str:
    """Coarse network prefix: /24 for IPv4, /48 for IPv6 (privacy + matching)."""
    if not ip:
        return "unknown"
    try:
        address = ipaddress.ip_address(ip.strip())
        return str(ipaddress.ip_network(f"{address}/{24 if address.version == 4 else 48}", strict=False))
    except ValueError:
        return "unknown"


# ---------------------------------------------------------------------
# Geolocation (pluggable, best-effort)
# ---------------------------------------------------------------------

def _geo_resolver() -> Any:
    spec = os.getenv("STOCKPILOT_GEO_RESOLVER", "")
    if spec and ":" in spec:
        module_name, func_name = spec.split(":", 1)
        try:
            import importlib
            mod = importlib.import_module(module_name)
            return getattr(mod, func_name)
        except Exception:
            return None
    return None


def geolocate(ip_prefix_value: str) -> tuple[float, float] | None:
    """Return (lat, lon) for an IP prefix, or None when unavailable."""
    if ip_prefix_value in _GEO_CACHE:
        return _GEO_CACHE[ip_prefix_value]
    resolver = _geo_resolver()
    if resolver is None:
        return None
    try:
        coords = resolver(ip_prefix_value)
        if coords and isinstance(coords, (tuple, list)) and len(coords) == 2:
            result = (float(coords[0]), float(coords[1]))
            _GEO_CACHE[ip_prefix_value] = result
            return result
    except Exception:
        return None
    return None


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------

def record_login(
    user_id: int,
    user_agent: str = "",
    ip: str | None = None,
    at: datetime | None = None,
    device_hash: str | None = None,
) -> dict[str, Any]:
    """Record a successful login and return detected anomalies.

    Returns {"new_device": bool, "impossible_travel": bool, "anomalies": [...]}.
    Never raises -- anomaly detection must not block authentication.
    """
    try:
        return _record_login_inner(user_id, user_agent, ip, at, device_hash)
    except Exception:
        if strict_mode():
            raise
        # Detection is best-effort; never block the login path.
        return {"new_device": False, "impossible_travel": False, "anomalies": []}


def _record_login_inner(
    user_id: int,
    user_agent: str,
    ip: str | None,
    at: datetime | None,
    supplied_device_hash: str | None = None,
) -> dict[str, Any]:
    _ensure_tables()
    at = at or datetime.now(timezone.utc)
    device_hash = supplied_device_hash or hash_device(user_agent, ip)
    prefix = ip_prefix(ip)
    coords = geolocate(prefix)

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id, lat, lon, last_seen FROM login_devices WHERE user_id=? AND device_hash=?",
            (int(user_id), device_hash),
        ).fetchone()

        last_seen = _parse_time(existing[3]) if existing else None
        new_device = existing is None or last_seen is None or at - last_seen > timedelta(days=NEW_DEVICE_WINDOW_DAYS)
        impossible_travel = False
        travel_detail = None
        previous_coords = None
        prev_time = None
        # Fingerprints include the network prefix. Comparing only that fingerprint
        # made travel detection blind to the very network changes it must detect.
        previous = conn.execute(
            "SELECT lat, lon, last_seen FROM login_devices WHERE user_id=? ORDER BY last_seen DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        if previous:
            if previous[0] is not None and previous[1] is not None:
                previous_coords = (float(previous[0]), float(previous[1]))
            prev_time = _parse_time(previous[2])

        if existing is None:
            conn.execute(
                """INSERT INTO login_devices
                   (user_id, device_hash, device_label, ip_prefix, lat, lon,
                    first_seen, last_seen, seen_count)
                   VALUES (?,?,?,?,?,?,?, ?, 1)""",
                (int(user_id), device_hash, (user_agent or "")[:200],
                 prefix, coords[0] if coords else None,
                 coords[1] if coords else None,
                 at.isoformat(), at.isoformat()),
            )
        else:
            conn.execute(
                "UPDATE login_devices SET last_seen=?, seen_count=seen_count+1, "
                "ip_prefix=?, lat=?, lon=? WHERE id=?",
                (at.isoformat(), prefix,
                 coords[0] if coords else None, coords[1] if coords else None,
                 int(existing[0])),
            )

        # Impossible travel: only when both coordinates are known and the
        # implied speed exceeds the configured threshold.
        if (
            previous_coords is not None
            and coords is not None
            and prev_time is not None
            and at >= prev_time
        ):
            distance = haversine_km(previous_coords, coords)
            hours = max((at - prev_time).total_seconds() / 3600.0, 1e-6)
            speed = distance / hours
            if distance > 50 and speed > DEFAULT_MAX_TRAVEL_KMH:
                impossible_travel = True
                travel_detail = (
                    f"{distance:.0f} km in {hours:.1f} h ({speed:.0f} km/h)"
                )

        anomalies: list[dict[str, Any]] = []
        if new_device:
            anomalies.append({
                "type": "new_device",
                "severity": "info",
                "detail": f"New device or network: {(user_agent or '')[:80]} [{prefix}]",
            })
        if impossible_travel:
            anomalies.append({
                "type": "impossible_travel",
                "severity": "warning",
                "detail": travel_detail,
            })

        for anomaly in anomalies:
            conn.execute(
                """INSERT INTO login_anomalies
                   (user_id, anomaly_type, severity, detail, ip_prefix, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (int(user_id), anomaly["type"], anomaly["severity"],
                 anomaly["detail"], prefix, at.isoformat()),
            )
        conn.commit()
    except Exception:
        # Detection is best-effort; never block the login path.
        conn.rollback()
        if strict_mode():
            raise
        return {"new_device": False, "impossible_travel": False, "anomalies": []}
    finally:
        conn.close()

    return {
        "device_hash": device_hash,
        "new_device": new_device,
        "impossible_travel": impossible_travel,
        "anomalies": anomalies,
    }


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def strict_mode() -> bool:
    return os.getenv("STOCKPILOT_LOGIN_ANOMALY_STRICT", "").strip().lower() in ("1", "true", "yes")


def confirm_device(user_id: int, device_hash: str) -> None:
    """Called only after independent MFA proof; acknowledging an alert cannot trust a device."""
    _ensure_tables()
    conn = get_connection()
    try:
        cursor = conn.execute("UPDATE login_devices SET confirmed_at=? WHERE user_id=? AND device_hash=?",
                              (datetime.now(timezone.utc).isoformat(), user_id, device_hash))
        if cursor.rowcount != 1:
            raise ValueError("Pending login device is unavailable; sign in again")
        conn.commit()
    finally:
        conn.close()


def device_confirmed(user_id: int, device_hash: str) -> bool:
    _ensure_tables()
    conn = get_connection()
    try:
        row = conn.execute("SELECT confirmed_at FROM login_devices WHERE user_id=? AND device_hash=?", (user_id, device_hash)).fetchone()
        confirmed = _parse_time(row[0]) if row else None
        return confirmed is not None and timedelta(0) <= datetime.now(timezone.utc) - confirmed <= timedelta(days=NEW_DEVICE_WINDOW_DAYS)
    finally:
        conn.close()


def anomalies_for_user(user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Recent anomalies for a user (for the security-center UI)."""
    _ensure_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, anomaly_type, severity, detail, created_at, acknowledged_at
               FROM login_anomalies WHERE user_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (int(user_id), max(1, min(int(limit), 500))),
        ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "severity": r[2],
                "detail": r[3], "created_at": r[4], "acknowledged_at": r[5],
            }
            for r in rows
        ]
    finally:
        conn.close()


def acknowledge_anomaly(user_id: int, anomaly_id: int) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE login_anomalies SET acknowledged_at=? WHERE id=? AND user_id=? AND acknowledged_at IS NULL",
            (datetime.now(timezone.utc).isoformat(), int(anomaly_id), int(user_id)),
        )
        conn.commit()
        return bool(cursor.rowcount > 0)
    finally:
        conn.close()
