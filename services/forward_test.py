"""Forward-test tracking for saved builder strategies (v9 Part F3).

"Watch this strategy live": once started, each evaluation re-runs a saved
strategy against the newest history and appends any *new* signal to a running
scorecard, so a user can see how the rules would have behaved from the day they
started watching rather than only in a backtest.

Boundaries:

- `ORDER_PLACEMENT = "never"`. This module does not place, queue, or
  would-place a real order, and does not import a broker client. Signals are
  recorded as observations; routing them to the paper simulator remains an
  explicit, separate user action.
- Evaluations are idempotent per (test, symbol, bar, action), so re-running the
  same day twice cannot inflate a scorecard.
- Scorecards report realized paired trades only; an open position is reported
  separately and excluded from win rate.
"""

from __future__ import annotations

import json
import math
import sqlite3
from services.db.base import DatabaseInterface
from services.db.sqlite_impl import SQLiteDatabase
from services.db.factory import get_database
from services.db.configuration import postgres_selected
from datetime import datetime, timezone
from typing import Any, Callable, Sequence, cast

from services.strategy_builder import StrategyError, evaluate_strategy

FORWARD_TEST_FLAG = "forward_test_tracking"
ORDER_PLACEMENT = "never"

MAX_ACTIVE_TESTS = 20
MAX_EVENTS_RETURNED = 200

FORWARD_TEST_DISCLOSURES: tuple[str, ...] = (
    "Forward tests record what a saved rule set would have signalled from the start date onward. They are not forecasts.",
    "No order is placed, queued, or routed to any broker by forward-test tracking.",
    "Signals are evaluated on end-of-session data, so intraday behaviour is not captured.",
    "A forward test is descriptive evidence about rules, not investment advice.",
)


class ForwardTestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _round(value: Any, digits: int = 2) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


class ForwardTestStore:
    """SQLite persistence for forward tests and their observed signals."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection] | None = None) -> None:
        self._connection_factory = connection_factory

    def _connect(self) -> DatabaseInterface:
        if self._connection_factory is not None:
            return SQLiteDatabase(self._connection_factory())
        return get_database()

    def ensure_schema(self) -> None:
        connection = self._connect()
        try:
            if self._connection_factory is None and postgres_selected():
                connection.fetchall("SELECT id FROM forward_tests LIMIT 0")
                connection.fetchall("SELECT id FROM forward_test_events LIMIT 0")
                return
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    strategy_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    symbols TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    stopped_at TEXT,
                    last_evaluated_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_test_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    forward_test_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    bar_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    reason TEXT,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(forward_test_id, symbol, bar_at, action)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def start(
        self,
        user_id: int,
        *,
        strategy_id: int,
        name: str,
        symbols: Sequence[str],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        self.ensure_schema()
        cleaned = [str(item).strip().upper() for item in symbols if str(item).strip()]
        cleaned = list(dict.fromkeys(cleaned))
        if not cleaned:
            raise ForwardTestError("forward_test_symbols_required", "At least one symbol is required.")
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        connection = self._connect()
        try:
            connection.begin_write("forward-tests:" + str(int(user_id)))
            if self._connection_factory is None and postgres_selected():
                owned = connection.fetchone(connection.sql("SELECT id FROM strategy_definitions WHERE id=? AND user_id=?"), (int(strategy_id), int(user_id)))
                if owned is None:
                    raise ForwardTestError("strategy_not_found", "No saved strategy with that id for this account.")
            active_row = connection.fetchone(connection.sql(
                "SELECT COUNT(*) AS n FROM forward_tests WHERE user_id = ? AND status = 'active'"),
                (int(user_id),),
            )
            active = int(active_row["n"]) if active_row else 0
            if int(active) >= MAX_ACTIVE_TESTS:
                raise ForwardTestError(
                    "forward_test_limit_reached",
                    f"At most {MAX_ACTIVE_TESTS} active forward tests are supported.",
                )
            existing = connection.fetchone(connection.sql(
                "SELECT id FROM forward_tests WHERE user_id = ? AND strategy_id = ? AND status = 'active'"),
                (int(user_id), int(strategy_id)),
            )
            if existing is not None:
                raise ForwardTestError(
                    "forward_test_already_active",
                    "This strategy is already being forward-tested.",
                )
            cursor = connection.execute(connection.sql(
                "INSERT INTO forward_tests (user_id, strategy_id, name, symbols, status, started_at) VALUES (?,?,?,?,'active',?) RETURNING id"),
                (int(user_id), int(strategy_id), str(name).strip() or f"Strategy {strategy_id}", json.dumps(cleaned), stamp),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                raise RuntimeError("Forward test did not receive an id.")
            test_id = int(inserted["id"])
            connection.commit()
        finally:
            connection.close()
        return {
            "id": test_id,
            "strategy_id": int(strategy_id),
            "name": str(name).strip() or f"Strategy {strategy_id}",
            "symbols": cleaned,
            "status": "active",
            "started_at": stamp,
            "order_placement": ORDER_PLACEMENT,
        }

    def stop(self, user_id: int, test_id: int, *, now: datetime | None = None) -> dict[str, Any]:
        self.ensure_schema()
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        connection = self._connect()
        try:
            connection.begin_write("forward-test:" + str(int(test_id)))
            cursor = connection.execute(connection.sql(
                "UPDATE forward_tests SET status = 'stopped', stopped_at = ? WHERE user_id = ? AND id = ? AND status = 'active'"),
                (stamp, int(user_id), int(test_id)),
            )
            connection.commit()
            if cursor.rowcount == 0:
                raise ForwardTestError("forward_test_not_found", "No active forward test with that id.")
        finally:
            connection.close()
        return {"id": int(test_id), "status": "stopped", "stopped_at": stamp}

    def get(self, user_id: int, test_id: int) -> dict[str, Any]:
        self.ensure_schema()
        connection = self._connect()
        try:
            row = connection.fetchone(connection.sql(
                "SELECT id, strategy_id, name, symbols, status, started_at, stopped_at, last_evaluated_at FROM forward_tests WHERE user_id = ? AND id = ?"),
                (int(user_id), int(test_id)),
            )
        finally:
            connection.close()
        if row is None:
            raise ForwardTestError("forward_test_not_found", "No forward test with that id.")
        return {
            "id": int(row["id"]),
            "strategy_id": int(row["strategy_id"]),
            "name": row["name"],
            "symbols": json.loads(row["symbols"]),
            "status": row["status"],
            "started_at": row["started_at"],
            "stopped_at": row["stopped_at"],
            "last_evaluated_at": row["last_evaluated_at"],
            "order_placement": ORDER_PLACEMENT,
        }

    def list_tests(self, user_id: int) -> list[dict[str, Any]]:
        self.ensure_schema()
        connection = self._connect()
        try:
            rows = connection.fetchall(connection.sql(
                "SELECT id, strategy_id, name, symbols, status, started_at, stopped_at, last_evaluated_at FROM forward_tests WHERE user_id = ? ORDER BY started_at DESC"),
                (int(user_id),),
            )
        finally:
            connection.close()
        return [
            {
                "id": int(row["id"]),
                "strategy_id": int(row["strategy_id"]),
                "name": row["name"],
                "symbols": json.loads(row["symbols"]),
                "status": row["status"],
                "started_at": row["started_at"],
                "stopped_at": row["stopped_at"],
                "last_evaluated_at": row["last_evaluated_at"],
                "order_placement": ORDER_PLACEMENT,
            }
            for row in rows
        ]

    def _record_events(self, test_id: int, events: Sequence[dict[str, Any]], *, stamp: str) -> int:
        if not events:
            return 0
        connection = self._connect()
        try:
            connection.begin_write("forward-test:" + str(int(test_id)))
            parent = connection.fetchone(connection.sql("SELECT status FROM forward_tests WHERE id=?"), (int(test_id),))
            if parent is None or parent["status"] != "active":
                raise ForwardTestError("forward_test_inactive", "This forward test is not active.")
            inserted = 0
            for event in events:
                cursor = connection.execute(connection.sql(
                    "INSERT INTO forward_test_events (forward_test_id, symbol, bar_at, action, price, reason, recorded_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(forward_test_id,symbol,bar_at,action) DO NOTHING"),
                    (
                        int(test_id),
                        str(event["symbol"]),
                        str(event["bar_at"]),
                        str(event["action"]),
                        float(event["price"]),
                        event.get("reason"),
                        stamp,
                    ),
                )
                inserted += cursor.rowcount if cursor.rowcount > 0 else 0
            connection.execute(connection.sql(
                "UPDATE forward_tests SET last_evaluated_at = ? WHERE id = ?"),
                (stamp, int(test_id)),
            )
            connection.commit()
            return inserted
        finally:
            connection.close()

    def events(self, test_id: int, *, limit: int = MAX_EVENTS_RETURNED) -> list[dict[str, Any]]:
        self.ensure_schema()
        connection = self._connect()
        try:
            rows = connection.fetchall(connection.sql(
                "SELECT symbol, bar_at, action, price, reason, recorded_at FROM forward_test_events WHERE forward_test_id = ? ORDER BY bar_at ASC, id ASC LIMIT ?"),
                (int(test_id), int(limit)),
            )
        finally:
            connection.close()
        return [
            {
                "symbol": row["symbol"],
                "bar_at": row["bar_at"],
                "action": row["action"],
                "price": _round(row["price"], 4),
                "reason": row["reason"],
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        ]

    def evaluate(
        self,
        user_id: int,
        test_id: int,
        *,
        strategy: dict[str, Any],
        history_loader: Callable[[str], Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Re-evaluate a forward test and append any newly observed signals.

        Only signals dated on or after the test's start are recorded, so
        starting a test cannot back-fill history and flatter the scorecard.
        """
        test = self.get(user_id, test_id)
        if test["status"] != "active":
            raise ForwardTestError("forward_test_inactive", "This forward test is not active.")
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        started_at = str(test["started_at"])

        observed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for symbol in test["symbols"]:
            try:
                frame = history_loader(symbol)
            except Exception as error:  # noqa: BLE001 - provider failure is data
                skipped.append({"symbol": symbol, "reason": "history_unavailable", "detail": type(error).__name__})
                continue
            try:
                evaluation = evaluate_strategy(strategy, frame, symbol=symbol, max_signals=0)
            except StrategyError as error:
                skipped.append({"symbol": symbol, "reason": error.code})
                continue
            if not evaluation.get("evaluated"):
                skipped.append({"symbol": symbol, "reason": evaluation.get("reason", "insufficient_history")})
                continue
            for signal in evaluation["signals"]:
                if str(signal["at"]) < started_at[:10]:
                    continue
                observed.append(
                    {
                        "symbol": symbol,
                        "bar_at": signal["at"],
                        "action": signal["action"],
                        "price": signal["price"],
                        "reason": signal.get("reason"),
                    }
                )

        recorded = self._record_events(test_id, observed, stamp=stamp)
        return {
            "forward_test": self.get(user_id, test_id),
            "evaluated_at": stamp,
            "new_signals": recorded,
            "signals_considered": len(observed),
            "skipped": skipped,
            "order_placement": ORDER_PLACEMENT,
            "disclosures": list(FORWARD_TEST_DISCLOSURES),
        }

    def scorecard(self, user_id: int, test_id: int) -> dict[str, Any]:
        """Running scorecard built from recorded, paired BUY/SELL observations."""
        test = self.get(user_id, test_id)
        events = self.events(test_id, limit=10_000)

        per_symbol: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            per_symbol.setdefault(event["symbol"], []).append(event)

        trades: list[dict[str, Any]] = []
        open_positions: list[dict[str, Any]] = []
        for symbol, rows in per_symbol.items():
            entry: dict[str, Any] | None = None
            for row in rows:
                if row["action"] == "BUY" and entry is None:
                    entry = row
                elif row["action"] == "SELL" and entry is not None:
                    entry_price = float(entry["price"])
                    exit_price = float(row["price"])
                    trades.append(
                        {
                            "symbol": symbol,
                            "entry_at": entry["bar_at"],
                            "exit_at": row["bar_at"],
                            "entry_price": _round(entry_price, 4),
                            "exit_price": _round(exit_price, 4),
                            "exit_reason": row.get("reason"),
                            "return_pct": _round((exit_price / entry_price - 1) * 100),
                        }
                    )
                    entry = None
            if entry is not None:
                open_positions.append({"symbol": symbol, "entry_at": entry["bar_at"], "entry_price": _round(entry["price"], 4)})

        returns = [trade["return_pct"] for trade in trades if trade["return_pct"] is not None]
        wins = [value for value in returns if value > 0]

        equity = 100.0
        peak = 100.0
        drawdown = 0.0
        for value in returns:
            equity *= 1 + value / 100
            peak = max(peak, equity)
            drawdown = min(drawdown, (equity - peak) / peak * 100)

        return {
            "forward_test": test,
            "scorecard": {
                "closed_trades": len(trades),
                "wins": len(wins),
                "losses": len(returns) - len(wins),
                "win_rate_pct": _round(len(wins) / len(returns) * 100) if returns else None,
                "avg_return_pct": _round(sum(returns) / len(returns)) if returns else None,
                "best_return_pct": _round(max(returns)) if returns else None,
                "worst_return_pct": _round(min(returns)) if returns else None,
                "max_drawdown_pct": _round(drawdown) if returns else None,
                "compounded_return_pct": _round(equity - 100) if returns else None,
                "basis": "Signal-to-signal closes, gross of costs. Paper observation only.",
            },
            "trades": trades,
            "open_positions": open_positions,
            "observed_signals": len(events),
            "order_placement": ORDER_PLACEMENT,
            "evidence": {
                "basis": "Recorded end-of-session rule matches from the start date onward.",
                "is_forecast": False,
                "is_recommendation": False,
            },
            "disclosures": list(FORWARD_TEST_DISCLOSURES),
        }


FORWARD_TESTS = ForwardTestStore()
