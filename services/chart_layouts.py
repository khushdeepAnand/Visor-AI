"""Per-user saved chart layouts (K2).

A layout captures the selected symbol, timeframe, overlay toggles, and the
chart's visible time-scale range so a user can restore the exact view later
(TradingView-style saved layouts). Layouts are stored per user in SQLite using
the same connection-factory pattern as `services/screener.py`.

All values are reference data, never trades: restoring a layout only navigates
a chart. Payloads are stored as JSON and never executed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

try:  # package layout
    from database import get_connection as _default_get_connection
except Exception:  # pragma: no cover - standalone/flat use
    _default_get_connection = None  # type: ignore[assignment]

ConnectionFactory = Callable[[], sqlite3.Connection]

MAX_SAVED_LAYOUTS = 25
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1D", "1W")
OVERLAY_KEYS = ("ema", "vwap", "band")


class ChartLayoutError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _validated_overlays(overlays: Iterable[Any] | None) -> dict[str, bool]:
    if not overlays:
        return {}
    cleaned: dict[str, bool] = {}
    for key, value in overlays.items() if isinstance(overlays, dict) else []:
        if str(key) in OVERLAY_KEYS:
            cleaned[str(key)] = bool(value)
    return cleaned


class ChartLayoutStore:
    def __init__(self, connection_factory: ConnectionFactory | None = None) -> None:
        self._factory = connection_factory

    def _connect(self) -> sqlite3.Connection:
        factory = self._factory or _default_get_connection
        if factory is None:  # pragma: no cover - only in a broken install
            raise ChartLayoutError("chart_layout_storage_unavailable", "No database connection factory is available.")
        connection = factory()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS saved_chart_layouts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                overlays_json TEXT NOT NULL DEFAULT '{}',
                visible_range_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            );
            """
        )
        connection.commit()
        return connection

    @staticmethod
    def _visible_range(value: str | None) -> dict[str, Any] | None:
        if not value:
            return None
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            (float(data["from"]), float(data["to"]))
        except (KeyError, TypeError, ValueError):
            return None
        return {"from": float(data["from"]), "to": float(data["to"])}

    @staticmethod
    def _row(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "name": row[1],
            "symbol": row[2],
            "timeframe": row[3],
            "overlays": json.loads(row[4]) if row[4] else {},
            "visible_range": ChartLayoutStore._visible_range(row[5]),
            "created_at": row[6],
            "updated_at": row[7],
        }

    def save_layout(
        self,
        *,
        user_id: int,
        name: str,
        symbol: str,
        timeframe: str,
        overlays: dict[str, Any] | None = None,
        visible_range: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        label = str(name or "").strip()
        if not 1 <= len(label) <= 60:
            raise ChartLayoutError("chart_layout_name_invalid", "Layout name must be 1-60 characters.")
        normalized_symbol = str(symbol or "").strip().upper()
        if not normalized_symbol:
            raise ChartLayoutError("chart_layout_symbol_required", "A layout needs a symbol.")
        timeframe = str(timeframe or "1D")
        if timeframe not in TIMEFRAMES:
            raise ChartLayoutError("chart_layout_timeframe_unknown", f"Unknown timeframe '{timeframe}'.")
        overlays_clean = _validated_overlays(overlays)
        range_clean: dict[str, float] | None = None
        if visible_range:
            try:
                range_clean = {"from": float(visible_range["from"]), "to": float(visible_range["to"])}
            except (KeyError, TypeError, ValueError):
                raise ChartLayoutError("chart_layout_range_invalid", "visible_range must contain numeric 'from' and 'to'.")
        stamp = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT COUNT(*) FROM saved_chart_layouts WHERE user_id = ? AND name != ?",
                (int(user_id), label),
            ).fetchone()
            if existing and int(existing[0]) >= MAX_SAVED_LAYOUTS:
                raise ChartLayoutError(
                    "chart_layout_saved_limit",
                    f"A maximum of {MAX_SAVED_LAYOUTS} saved layouts per account is supported.",
                )
            connection.execute(
                """
                INSERT INTO saved_chart_layouts(user_id, name, symbol, timeframe, overlays_json, visible_range_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    symbol = excluded.symbol,
                    timeframe = excluded.timeframe,
                    overlays_json = excluded.overlays_json,
                    visible_range_json = excluded.visible_range_json,
                    updated_at = excluded.updated_at
                """,
                (
                    int(user_id),
                    label,
                    normalized_symbol,
                    timeframe,
                    json.dumps(overlays_clean),
                    json.dumps(range_clean) if range_clean else None,
                    stamp,
                    stamp,
                ),
            )
            connection.commit()
            row = connection.execute(
                """
                SELECT id, name, symbol, timeframe, overlays_json, visible_range_json, created_at, updated_at
                FROM saved_chart_layouts WHERE user_id = ? AND name = ?
                """,
                (int(user_id), label),
            ).fetchone()
        finally:
            connection.close()
        if row is None:  # pragma: no cover - defensive
            raise ChartLayoutError("chart_layout_storage_unavailable", "The layout could not be stored.")
        return self._row(row)

    def list_layouts(self, *, user_id: int) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, name, symbol, timeframe, overlays_json, visible_range_json, created_at, updated_at
                FROM saved_chart_layouts WHERE user_id = ? ORDER BY updated_at DESC
                """,
                (int(user_id),),
            ).fetchall()
        finally:
            connection.close()
        return [self._row(row) for row in rows]

    def get_layout(self, *, user_id: int, layout_id: int) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id, name, symbol, timeframe, overlays_json, visible_range_json, created_at, updated_at
                FROM saved_chart_layouts WHERE user_id = ? AND id = ?
                """,
                (int(user_id), int(layout_id)),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise ChartLayoutError("chart_layout_not_found", "That chart layout does not exist for this account.")
        return self._row(row)

    def delete_layout(self, *, user_id: int, layout_id: int) -> dict[str, Any]:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM saved_chart_layouts WHERE user_id = ? AND id = ?",
                (int(user_id), int(layout_id)),
            )
            connection.commit()
            deleted = cursor.rowcount
        finally:
            connection.close()
        if not deleted:
            raise ChartLayoutError("chart_layout_not_found", "That chart layout does not exist for this account.")
        return {"deleted": True, "id": int(layout_id)}


#: Process-wide store used by the API layer.
CHART_LAYOUTS = ChartLayoutStore()