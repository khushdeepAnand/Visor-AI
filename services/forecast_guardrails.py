"""Operational guardrails: forecast kill switches, feature flags, status banners.

Intended repository path: ``services/forecast_guardrails.py``.

This module closes three gaps in section 6.9 ("Operations and Content") of the
v6.1 audit prompt:

1. a **forecast kill switch** that can be scoped by asset class, symbol,
   timeframe, or model version, with a required reason, an expiry, an audit
   entry, and a rollback path;
2. **allowlisted feature flags** with staged (percentage) rollout and an
   automatic rollback hook driven by observed failure metrics;
3. a **time-bounded application status banner** that must be previewed before
   it becomes user visible.

Design rules kept from the rest of the project:

* nothing here can execute an arbitrary payload; every writable name is drawn
  from a server-side allowlist;
* every state change is written to the existing sanitized admin audit log;
* records are bounded in size and always carry an explicit expiry, so a
  forgotten switch cannot silently disable the product forever;
* the store is plain SQLite through the project connection factory, so it works
  on the supported local-Windows deployment with no new dependency.
"""
from __future__ import annotations

import json
import sqlite3
from services.db.base import DatabaseInterface
from services.db.sqlite_impl import SQLiteDatabase
from services.db.factory import get_database
from services.db.configuration import postgres_selected
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

# ----------------------------------------------------------------------------
# Dependency resolution
#
# The release tree exposes these helpers under ``services.*``; the flat audit
# bundle exposes them at top level. Import defensively so the module is usable
# in both layouts without duplicating code.
# ----------------------------------------------------------------------------
try:  # pragma: no cover - layout dependent
    from database import get_connection as _default_get_connection
except Exception:  # pragma: no cover
    _default_get_connection = None  # type: ignore[assignment]

_record_admin_action: Any
try:  # pragma: no cover - layout dependent
    from services.admin_registry import record_admin_action as _packaged_record_admin_action
    _record_admin_action = _packaged_record_admin_action
except Exception:  # pragma: no cover
    try:
        from admin_registry import record_admin_action as _flat_record_admin_action  # type: ignore[import-not-found]
        _record_admin_action = _flat_record_admin_action
    except Exception:
        _record_admin_action = None

ConnectionFactory = Callable[[], sqlite3.Connection]

#: Scopes a kill switch may target. Anything else is rejected, so a switch can
#: never be pointed at an unbounded or free-text dimension.
KILL_SWITCH_SCOPES: tuple[str, ...] = ("asset_class", "symbol", "timeframe", "model_version", "global")

#: Asset classes the forecast surface understands.
ASSET_CLASSES: tuple[str, ...] = ("equity", "index", "futures", "options")

#: Maximum life of a kill switch. An operator must renew deliberately rather
#: than leave a permanent block behind after an incident.
MAX_KILL_SWITCH_HOURS = 24 * 14

#: Maximum life of a status banner.
MAX_BANNER_HOURS = 24 * 7

#: The only feature flags an administrator may stage. Each entry declares its
#: default, a plain-language description, and the automatic-rollback budget.
FEATURE_FLAGS: dict[str, dict[str, Any]] = {
    "pooled_low_history_model": {
        "default": True,
        "description": "Serve pooled cross-sectional research ranges for limited-history instruments.",
        "max_failure_rate": 0.10,
        "min_samples": 40,
    },
    "forecast_corridor_v2": {
        "default": True,
        "description": "Render the redesigned Forecast Corridor on the instrument detail screen.",
        "max_failure_rate": 0.05,
        "min_samples": 100,
    },
    "scenario_studio": {
        "default": True,
        "description": "Expose the non-prescriptive options/underlying Scenario Studio.",
        "max_failure_rate": 0.05,
        "min_samples": 100,
    },
    "daily_brief_digest": {
        "default": True,
        "description": "Send the opt-in daily watchlist research digest.",
        "max_failure_rate": 0.02,
        "min_samples": 50,
    },
    "strategy_builder": {
        "default": True,
        "description": "Expose the no-code rule builder and its paper-only equity backtester.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "multi_leg_backtest": {
        "default": True,
        "description": "Expose the model-priced multi-leg option cycle backtester.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "forward_test_tracking": {
        "default": True,
        "description": "Track saved strategies forward on stored history. Never places an order.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "screener_live_quotes": {
        "default": True,
        "description": "Refresh matching screener rows with live quotes after a run.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "saved_chart_layouts": {
        "default": True,
        "description": "Persist chart overlay/time-scale layout preferences per symbol.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "sector_rotation": {
        "default": True,
        "description": "Expose the sector coverage report and sector-grouped rotation view.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "sentiment_trend": {
        "default": True,
        "description": "Expose the per-symbol sentiment history trend.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
    "research_assistant": {
        "default": True,
        "description": "Ground-only conversational research assistant over a symbol's ingested data.",
        "max_failure_rate": 0.05,
        "min_samples": 50,
    },
}

MIN_REASON_LENGTH = 12
MAX_REASON_LENGTH = 240
MAX_BANNER_BODY = 400
MAX_ACTIVE_KILL_SWITCHES = 50

BANNER_LEVELS: tuple[str, ...] = ("info", "maintenance", "degraded", "outage")


class GuardrailError(ValueError):
    """An operator supplied an unusable guardrail request."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_reason(reason: str | None) -> str:
    text = " ".join(str(reason or "").split())
    if len(text) < MIN_REASON_LENGTH:
        raise GuardrailError(
            f"A reason of at least {MIN_REASON_LENGTH} characters is required so the audit log explains the change."
        )
    return text[:MAX_REASON_LENGTH]


def _normalize_target(scope: str, target: str | None) -> str:
    scope_key = str(scope or "").strip().lower()
    if scope_key not in KILL_SWITCH_SCOPES:
        raise GuardrailError(f"Unsupported kill-switch scope: {scope!r}. Allowed: {', '.join(KILL_SWITCH_SCOPES)}.")
    if scope_key == "global":
        return "*"
    value = str(target or "").strip()
    if not value:
        raise GuardrailError(f"Scope {scope_key!r} requires a target value.")
    if scope_key == "asset_class":
        lowered = value.lower()
        if lowered not in ASSET_CLASSES:
            raise GuardrailError(f"Unknown asset class {value!r}. Allowed: {', '.join(ASSET_CLASSES)}.")
        return lowered
    if scope_key == "symbol":
        normalized = value.upper()
        if len(normalized) > 40 or not all(char.isalnum() or char in "-_.:&| " for char in normalized):
            raise GuardrailError("Symbol targets must be catalogue-style identifiers.")
        return normalized
    if scope_key == "timeframe":
        normalized = value.lower()
        if len(normalized) > 16:
            raise GuardrailError("Timeframe targets are short codes such as '1d' or '15m'.")
        return normalized
    normalized = value.strip()
    if len(normalized) > 60:
        raise GuardrailError("Model-version targets must be short registry versions.")
    return normalized


@dataclass(frozen=True)
class KillSwitch:
    id: int
    scope: str
    target: str
    reason: str
    created_by: str | None
    created_at: str
    expires_at: str
    revoked_at: str | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "target": self.target,
            "reason": self.reason,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "revoked_by": self.revoked_by,
            "revoke_reason": self.revoke_reason,
            "active": self.revoked_at is None,
        }


@dataclass
class ForecastRequestContext:
    """The dimensions a kill switch can match against one forecast request."""

    symbol: str = ""
    asset_class: str = "equity"
    timeframe: str = ""
    model_version: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class GuardrailStore:
    """SQLite-backed guardrail state with audit-logged mutations."""

    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._factory = connection_factory

    # -- infrastructure ----------------------------------------------------
    def _connect(self) -> DatabaseInterface:
        if self._factory is None and postgres_selected():
            db = get_database()
            try:
                db.fetchall("SELECT id FROM forecast_kill_switches LIMIT 0")
                db.fetchall("SELECT name FROM feature_flag_state LIMIT 0")
                db.fetchall("SELECT id FROM status_banners LIMIT 0")
                return db
            except Exception:
                db.close()
                raise
        factory = self._factory or _default_get_connection
        if factory is None:  # pragma: no cover - only in a broken install
            raise GuardrailError("No database connection factory is available.")
        connection = factory()
        self._ensure_schema(connection)
        return SQLiteDatabase(connection)

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS forecast_kill_switches(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                target TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                revoked_by TEXT,
                revoke_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS feature_flag_state(
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                rollout_percent INTEGER NOT NULL DEFAULT 0,
                updated_by TEXT,
                updated_at TEXT NOT NULL,
                reason TEXT,
                auto_rolled_back_at TEXT,
                auto_rollback_detail TEXT
            );
            CREATE TABLE IF NOT EXISTS status_banners(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                headline TEXT NOT NULL,
                body TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_at TEXT NOT NULL,
                published_by TEXT,
                published_at TEXT,
                withdrawn_at TEXT
            );
            """
        )
        connection.commit()

    @staticmethod
    def _audit(
        *,
        actor_id: int | None,
        actor_email: str | None,
        action: str,
        target: str | None,
        outcome: str = "ok",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if _record_admin_action is None:  # pragma: no cover - standalone use
            return
        try:
            _record_admin_action(
                actor_id=actor_id,
                actor_email=actor_email,
                action=action,
                target=target,
                outcome=outcome,
                detail=detail,
            )
        except Exception:  # pragma: no cover - auditing must not break the action
            pass

    # -- kill switches -----------------------------------------------------
    def create_kill_switch(
        self,
        *,
        scope: str,
        target: str | None,
        reason: str,
        expires_in_hours: float,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        """Block forecasts for one bounded scope until an explicit expiry."""
        normalized_scope = str(scope or "").strip().lower()
        normalized_target = _normalize_target(normalized_scope, target)
        clean_reason = _clean_reason(reason)
        try:
            hours = float(expires_in_hours)
        except (TypeError, ValueError) as error:
            raise GuardrailError("expires_in_hours must be a number.") from error
        if not 0 < hours <= MAX_KILL_SWITCH_HOURS:
            raise GuardrailError(f"expires_in_hours must be between 0 and {MAX_KILL_SWITCH_HOURS}.")

        now = _utc_now()
        expires_at = now + timedelta(hours=hours)
        connection = self._connect()
        try:
            connection.begin_write("forecast-kill-switches")
            active_row = connection.fetchone(connection.sql(
                "SELECT COUNT(*) AS n FROM forecast_kill_switches WHERE revoked_at IS NULL AND expires_at > ?"),
                (_iso(now),),
            )
            active = int(active_row["n"]) if active_row else 0
            if int(active) >= MAX_ACTIVE_KILL_SWITCHES:
                raise GuardrailError("Too many active kill switches; revoke stale entries first.")
            cursor = connection.execute(connection.sql(
                """INSERT INTO forecast_kill_switches(scope, target, reason, created_by, created_at, expires_at)
                   VALUES(?,?,?,?,?,?) RETURNING id"""),
                (normalized_scope, normalized_target, clean_reason, actor_email, _iso(now), _iso(expires_at)),
            )
            switch_id = int(cursor.fetchone()["id"])
            connection.commit()
        finally:
            connection.close()

        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="forecast_kill_switch_created",
            target=f"{normalized_scope}:{normalized_target}",
            detail={"reason": clean_reason, "expires_at": _iso(expires_at), "switch_id": switch_id},
        )
        return {
            "id": switch_id,
            "scope": normalized_scope,
            "target": normalized_target,
            "reason": clean_reason,
            "created_at": _iso(now),
            "expires_at": _iso(expires_at),
            "active": True,
        }

    def revoke_kill_switch(
        self,
        switch_id: int,
        *,
        reason: str,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        """Roll a kill switch back. This is the documented recovery path."""
        clean_reason = _clean_reason(reason)
        now = _utc_now()
        connection = self._connect()
        try:
            connection.begin_write("forecast-kill-switches")
            row = connection.fetchone(connection.sql(
                "SELECT id, scope, target, revoked_at FROM forecast_kill_switches WHERE id=?"),
                (int(switch_id),),
            )
            if row is None:
                raise GuardrailError("That kill switch does not exist.")
            if row["revoked_at"] is not None:
                raise GuardrailError("That kill switch was already revoked.")
            connection.execute(connection.sql(
                "UPDATE forecast_kill_switches SET revoked_at=?, revoked_by=?, revoke_reason=? WHERE id=?"),
                (_iso(now), actor_email, clean_reason, int(switch_id)),
            )
            connection.commit()
            scope, target = row["scope"], row["target"]
        finally:
            connection.close()

        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="forecast_kill_switch_revoked",
            target=f"{scope}:{target}",
            detail={"reason": clean_reason, "switch_id": int(switch_id)},
        )
        return {"id": int(switch_id), "scope": scope, "target": target, "active": False, "revoked_at": _iso(now)}

    def list_kill_switches(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.fetchall(
                """SELECT id, scope, target, reason, created_by, created_at, expires_at,
                          revoked_at, revoked_by, revoke_reason
                   FROM forecast_kill_switches ORDER BY id DESC LIMIT 500"""
            )
        finally:
            connection.close()
        now = _utc_now()
        result: list[dict[str, Any]] = []
        for row in rows:
            switch = KillSwitch(**row).as_dict()
            expires = _parse_iso(switch["expires_at"])
            expired = expires is not None and expires <= now
            switch["expired"] = expired
            switch["active"] = switch["revoked_at"] is None and not expired
            if switch["active"] or include_inactive:
                result.append(switch)
        return result

    def evaluate_forecast_request(self, context: ForecastRequestContext | dict[str, Any]) -> dict[str, Any]:
        """Return the blocking decision for one forecast request.

        The result is deliberately shaped like the rest of the forecast
        contract: a stable code plus a plain-language explanation, so the
        frontend can render the existing designed abstention card instead of a
        generic error.
        """
        if isinstance(context, dict):
            context = ForecastRequestContext(
                symbol=str(context.get("symbol") or ""),
                asset_class=str(context.get("asset_class") or "equity"),
                timeframe=str(context.get("timeframe") or ""),
                model_version=str(context.get("model_version") or ""),
            )
        candidates = {
            "global": "*",
            "asset_class": str(context.asset_class or "").strip().lower(),
            "symbol": str(context.symbol or "").strip().upper(),
            "timeframe": str(context.timeframe or "").strip().lower(),
            "model_version": str(context.model_version or "").strip(),
        }
        for switch in self.list_kill_switches():
            expected = candidates.get(switch["scope"])
            if expected and switch["target"] == expected:
                return {
                    "blocked": True,
                    "code": "forecast_disabled_by_operator",
                    "state": "blocked",
                    "scope": switch["scope"],
                    "target": switch["target"],
                    "reason": switch["reason"],
                    "expires_at": switch["expires_at"],
                    "message": (
                        "Forecasts are paused for this selection by an operator. "
                        "Live prices and analytics are still available, and the pause "
                        f"is scheduled to end at {switch['expires_at']}."
                    ),
                }
        return {"blocked": False, "code": None, "state": "allowed"}

    # -- feature flags -----------------------------------------------------
    def flag_states(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = {
                str(row["name"]): row
                for row in connection.fetchall(
                    """SELECT name, enabled, rollout_percent, updated_by, updated_at, reason,
                              auto_rolled_back_at, auto_rollback_detail FROM feature_flag_state"""
                )
            }
        finally:
            connection.close()
        states: list[dict[str, Any]] = []
        for name, spec in FEATURE_FLAGS.items():
            row = rows.get(name)
            states.append(
                {
                    "name": name,
                    "description": spec["description"],
                    "default": bool(spec["default"]),
                    "enabled": bool(row["enabled"]) if row else bool(spec["default"]),
                    "rollout_percent": int(row["rollout_percent"]) if row else (100 if spec["default"] else 0),
                    "updated_by": row["updated_by"] if row else None,
                    "updated_at": row["updated_at"] if row else None,
                    "reason": row["reason"] if row else None,
                    "auto_rolled_back_at": row["auto_rolled_back_at"] if row else None,
                    "auto_rollback_detail": json.loads(row["auto_rollback_detail"]) if row and row["auto_rollback_detail"] else None,
                    "max_failure_rate": spec["max_failure_rate"],
                    "min_samples": spec["min_samples"],
                }
            )
        return states

    def set_feature_flag(
        self,
        name: str,
        *,
        enabled: bool,
        rollout_percent: int = 0,
        reason: str,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        key = str(name or "").strip()
        if key not in FEATURE_FLAGS:
            raise GuardrailError(f"Unknown feature flag {name!r}.")
        clean_reason = _clean_reason(reason)
        try:
            percent = int(rollout_percent)
        except (TypeError, ValueError) as error:
            raise GuardrailError("rollout_percent must be an integer between 0 and 100.") from error
        if not 0 <= percent <= 100:
            raise GuardrailError("rollout_percent must be between 0 and 100.")
        is_enabled = bool(enabled)
        if is_enabled and percent == 0:
            raise GuardrailError("Enabling a flag requires a rollout percentage above 0.")
        if not is_enabled:
            percent = 0

        now = _iso(_utc_now())
        connection = self._connect()
        try:
            connection.begin_write("feature-flag:" + key)
            connection.execute(connection.sql(
                """INSERT INTO feature_flag_state(name, enabled, rollout_percent, updated_by, updated_at, reason,
                                                  auto_rolled_back_at, auto_rollback_detail)
                   VALUES(?,?,?,?,?,?,NULL,NULL)
                   ON CONFLICT(name) DO UPDATE SET
                       enabled=excluded.enabled,
                       rollout_percent=excluded.rollout_percent,
                       updated_by=excluded.updated_by,
                       updated_at=excluded.updated_at,
                       reason=excluded.reason,
                       auto_rolled_back_at=NULL,
                        auto_rollback_detail=NULL"""),
                (key, 1 if is_enabled else 0, percent, actor_email, now, clean_reason),
            )
            connection.commit()
        finally:
            connection.close()

        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="feature_flag_updated",
            target=key,
            detail={"enabled": str(is_enabled), "rollout_percent": str(percent), "reason": clean_reason},
        )
        return {"name": key, "enabled": is_enabled, "rollout_percent": percent, "updated_at": now}

    def is_feature_enabled(self, name: str, *, subject: str | None = None) -> bool:
        """Deterministic staged rollout: the same subject always gets the same answer."""
        key = str(name or "").strip()
        if key not in FEATURE_FLAGS:
            return False
        state = next((item for item in self.flag_states() if item["name"] == key), None)
        if state is None or not state["enabled"]:
            return False
        percent = int(state["rollout_percent"])
        if percent >= 100:
            return True
        if percent <= 0:
            return False
        if not subject:
            return False
        import hashlib

        digest = hashlib.sha256(f"{key}:{subject}".encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % 100
        return bucket < percent

    def evaluate_auto_rollback(
        self,
        name: str,
        *,
        samples: int,
        failures: int,
        actor_email: str | None = None,
    ) -> dict[str, Any]:
        """Disable a staged flag automatically when its failure budget is exceeded.

        The caller supplies observed counts from the existing observability
        counters. Below ``min_samples`` nothing happens, because a handful of
        errors is not evidence.
        """
        key = str(name or "").strip()
        spec = FEATURE_FLAGS.get(key)
        if spec is None:
            raise GuardrailError(f"Unknown feature flag {name!r}.")
        total = max(0, int(samples))
        bad = max(0, min(int(failures), total))
        if total < int(spec["min_samples"]):
            return {"name": key, "action": "insufficient_samples", "samples": total, "failure_rate": None}
        rate = bad / total if total else 0.0
        if rate <= float(spec["max_failure_rate"]):
            return {"name": key, "action": "within_budget", "samples": total, "failure_rate": round(rate, 4)}

        now = _iso(_utc_now())
        detail = {"samples": total, "failures": bad, "failure_rate": round(rate, 4), "budget": spec["max_failure_rate"]}
        connection = self._connect()
        try:
            connection.begin_write("feature-flag:" + key)
            connection.execute(connection.sql(
                """INSERT INTO feature_flag_state(name, enabled, rollout_percent, updated_by, updated_at, reason,
                                                  auto_rolled_back_at, auto_rollback_detail)
                   VALUES(?,0,0,?,?,?,?,?)
                   ON CONFLICT(name) DO UPDATE SET
                       enabled=0,
                       rollout_percent=0,
                       updated_at=excluded.updated_at,
                       reason=excluded.reason,
                       auto_rolled_back_at=excluded.auto_rolled_back_at,
                        auto_rollback_detail=excluded.auto_rollback_detail"""),
                (
                    key,
                    actor_email or "system:auto-rollback",
                    now,
                    "Automatic rollback: observed failure rate exceeded the configured budget.",
                    now,
                    json.dumps(detail),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        self._audit(
            actor_id=None,
            actor_email=actor_email or "system:auto-rollback",
            action="feature_flag_auto_rolled_back",
            target=key,
            outcome="rolled_back",
            detail={str(field_name): str(value) for field_name, value in detail.items()},
        )
        return {"name": key, "action": "rolled_back", "samples": total, "failure_rate": round(rate, 4)}

    # -- status banners ----------------------------------------------------
    def draft_banner(
        self,
        *,
        level: str,
        headline: str,
        body: str,
        starts_at: str | datetime | None = None,
        ends_in_hours: float = 6.0,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        """Create an unpublished banner. Publication is a separate, audited step."""
        level_key = str(level or "").strip().lower()
        if level_key not in BANNER_LEVELS:
            raise GuardrailError(f"Unsupported banner level {level!r}. Allowed: {', '.join(BANNER_LEVELS)}.")
        clean_headline = " ".join(str(headline or "").split())[:120]
        clean_body = " ".join(str(body or "").split())[:MAX_BANNER_BODY]
        if len(clean_headline) < 4 or len(clean_body) < MIN_REASON_LENGTH:
            raise GuardrailError("A banner needs a short headline and an explanatory body.")
        try:
            hours = float(ends_in_hours)
        except (TypeError, ValueError) as error:
            raise GuardrailError("ends_in_hours must be a number.") from error
        if not 0 < hours <= MAX_BANNER_HOURS:
            raise GuardrailError(f"ends_in_hours must be between 0 and {MAX_BANNER_HOURS}.")

        start = starts_at if isinstance(starts_at, datetime) else _parse_iso(starts_at if isinstance(starts_at, str) else None)
        start = (start or _utc_now()).astimezone(timezone.utc)
        end = start + timedelta(hours=hours)
        now = _iso(_utc_now())
        connection = self._connect()
        try:
            cursor = connection.execute(connection.sql(
                """INSERT INTO status_banners(level, headline, body, starts_at, ends_at, published, created_by, created_at)
                   VALUES(?,?,?,?,?,0,?,?) RETURNING id"""),
                (level_key, clean_headline, clean_body, _iso(start), _iso(end), actor_email, now),
            )
            banner_id = int(cursor.fetchone()["id"])
            connection.commit()
        finally:
            connection.close()

        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="status_banner_drafted",
            target=str(banner_id),
            detail={"level": level_key, "starts_at": _iso(start), "ends_at": _iso(end)},
        )
        return {
            "id": banner_id,
            "level": level_key,
            "headline": clean_headline,
            "body": clean_body,
            "starts_at": _iso(start),
            "ends_at": _iso(end),
            "published": False,
            "preview_required": True,
        }

    def publish_banner(
        self,
        banner_id: int,
        *,
        confirmed_preview: bool,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        if not confirmed_preview:
            raise GuardrailError("A banner must be previewed and explicitly confirmed before publication.")
        now = _iso(_utc_now())
        connection = self._connect()
        try:
            connection.begin_write("status-banner:" + str(int(banner_id)))
            row = connection.fetchone(connection.sql(
                "SELECT id, published, withdrawn_at FROM status_banners WHERE id=?"),
                (int(banner_id),),
            )
            if row is None:
                raise GuardrailError("That banner does not exist.")
            if row["withdrawn_at"] is not None:
                raise GuardrailError("That banner was withdrawn; draft a new one.")
            connection.execute(connection.sql(
                "UPDATE status_banners SET published=1, published_by=?, published_at=? WHERE id=?"),
                (actor_email, now, int(banner_id)),
            )
            connection.commit()
        finally:
            connection.close()
        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="status_banner_published",
            target=str(int(banner_id)),
            detail={"published_at": now},
        )
        return {"id": int(banner_id), "published": True, "published_at": now}

    def withdraw_banner(
        self,
        banner_id: int,
        *,
        actor_email: str | None = None,
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        now = _iso(_utc_now())
        connection = self._connect()
        try:
            connection.begin_write("status-banner:" + str(int(banner_id)))
            updated = connection.execute(connection.sql(
                "UPDATE status_banners SET published=0, withdrawn_at=? WHERE id=? AND withdrawn_at IS NULL"),
                (now, int(banner_id)),
            ).rowcount
            connection.commit()
        finally:
            connection.close()
        if not updated:
            raise GuardrailError("That banner does not exist or was already withdrawn.")
        self._audit(
            actor_id=actor_id,
            actor_email=actor_email,
            action="status_banner_withdrawn",
            target=str(int(banner_id)),
            detail={"withdrawn_at": now},
        )
        return {"id": int(banner_id), "published": False, "withdrawn_at": now}

    def active_banners(self, *, at: datetime | None = None) -> list[dict[str, Any]]:
        """Banners a normal user should currently see."""
        moment = (at or _utc_now()).astimezone(timezone.utc)
        connection = self._connect()
        try:
            rows = connection.fetchall(
                """SELECT id, level, headline, body, starts_at, ends_at FROM status_banners
                   WHERE published=1 AND withdrawn_at IS NULL ORDER BY id DESC LIMIT 20"""
            )
        finally:
            connection.close()
        banners: list[dict[str, Any]] = []
        for row in rows:
            starts, ends = _parse_iso(row["starts_at"]), _parse_iso(row["ends_at"])
            if starts is None or ends is None:
                continue
            if starts <= moment < ends:
                banners.append(
                    {
                        "id": row["id"],
                        "level": row["level"],
                        "headline": row["headline"],
                        "body": row["body"],
                        "starts_at": row["starts_at"],
                        "ends_at": row["ends_at"],
                    }
                )
        return banners

    def list_banners(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.fetchall(
                """SELECT id, level, headline, body, starts_at, ends_at, published, created_by, created_at,
                          published_by, published_at, withdrawn_at
                   FROM status_banners ORDER BY id DESC LIMIT 200"""
            )
        finally:
            connection.close()
        keys = (
            "id",
            "level",
            "headline",
            "body",
            "starts_at",
            "ends_at",
            "published",
            "created_by",
            "created_at",
            "published_by",
            "published_at",
            "withdrawn_at",
        )
        result = []
        for row in rows:
            item = dict(row)
            item["published"] = bool(item["published"])
            result.append(item)
        return result

    # -- admin summary -----------------------------------------------------
    def operations_summary(self) -> dict[str, Any]:
        """Everything the admin "Operations and Content" section needs."""
        return {
            "kill_switches": {
                "scopes": list(KILL_SWITCH_SCOPES),
                "asset_classes": list(ASSET_CLASSES),
                "max_hours": MAX_KILL_SWITCH_HOURS,
                "active": self.list_kill_switches(),
            },
            "feature_flags": self.flag_states(),
            "banners": {
                "levels": list(BANNER_LEVELS),
                "max_hours": MAX_BANNER_HOURS,
                "active": self.active_banners(),
                "all": self.list_banners(),
            },
        }


#: Process-wide store used by the API layer.
GUARDRAILS = GuardrailStore()


def forecast_block_reason(context: ForecastRequestContext | dict[str, Any]) -> dict[str, Any]:
    """Convenience wrapper for the forecast path."""
    return GUARDRAILS.evaluate_forecast_request(context)


def public_status_payload() -> dict[str, Any]:
    """Public, non-privileged status content for the app shell."""
    return {"banners": GUARDRAILS.active_banners()}


def enabled_features(subject: str | None = None) -> dict[str, bool]:
    return {name: GUARDRAILS.is_feature_enabled(name, subject=subject) for name in FEATURE_FLAGS}


__all__: Sequence[str] = (
    "ASSET_CLASSES",
    "BANNER_LEVELS",
    "FEATURE_FLAGS",
    "ForecastRequestContext",
    "GUARDRAILS",
    "GuardrailError",
    "GuardrailStore",
    "KILL_SWITCH_SCOPES",
    "MAX_BANNER_HOURS",
    "MAX_KILL_SWITCH_HOURS",
    "enabled_features",
    "forecast_block_reason",
    "public_status_payload",
)
