"""Database-backed paper trading with cash and position controls."""

from __future__ import annotations

from typing import Any
import math

from database import record_audit_event
from services.db.factory import dao_session
from services.db.configuration import postgres_selected
from stock_api import normalize_symbol

DEFAULT_PAPER_BALANCE = 1_000_000.0


def ensure_paper_account(user_id: int, initial_balance: float = DEFAULT_PAPER_BALANCE) -> dict[str, float]:
    user_id = int(user_id)
    initial_balance = float(initial_balance)
    if not math.isfinite(initial_balance) or initial_balance <= 0:
        raise ValueError("Initial paper balance must be positive.")
    with dao_session() as factory:
        conn = factory.db
        conn.execute(conn.sql("INSERT INTO paper_accounts(user_id,initial_balance,cash_balance) VALUES(?,?,?) ON CONFLICT(user_id) DO NOTHING"),
                     (user_id, initial_balance, initial_balance))
        row = conn.fetchone(conn.sql("SELECT initial_balance,cash_balance FROM paper_accounts WHERE user_id=?"), (user_id,))
        if row is None:
            raise RuntimeError("Paper account could not be loaded")
        conn.commit()
        return {"initial_balance": float(row["initial_balance"]), "cash_balance": float(row["cash_balance"])}


def execute_paper_order(
    user_id: int,
    symbol: str,
    side: str,
    quantity: float,
    price: float,
) -> dict[str, Any]:
    """Execute an immediate simulated market fill at the supplied market price."""

    user_id = int(user_id)
    symbol = normalize_symbol(symbol)
    side = str(side or "").strip().upper()
    quantity = float(quantity)
    price = float(price)
    if side not in {"BUY", "SELL"}:
        raise ValueError("Paper order side must be BUY or SELL.")
    if not math.isfinite(quantity) or not math.isfinite(price) or quantity <= 0 or price <= 0:
        raise ValueError("Quantity and price must be positive.")
    notional = quantity * price
    ensure_paper_account(user_id)

    with dao_session() as factory:
        conn = factory.db
        conn.begin_write("paper-account:" + str(user_id))
        query = "SELECT cash_balance FROM paper_accounts WHERE user_id = ?"
        if postgres_selected():
            query += " FOR UPDATE"
        account = conn.fetchone(conn.sql(query),
            (user_id,),
        )
        if account is None:
            raise RuntimeError("Paper account could not be loaded")
        cash = float(account["cash_balance"])
        position = conn.fetchone(conn.sql(
            "SELECT id, quantity, average_price FROM paper_positions WHERE user_id = ? AND symbol = ?"),
            (user_id, symbol),
        )

        if side == "BUY":
            if notional > cash + 1e-9:
                raise ValueError("Insufficient paper cash for this order.")
            old_quantity = float(position["quantity"]) if position else 0.0
            old_average = float(position["average_price"]) if position else 0.0
            new_quantity = old_quantity + quantity
            new_average = ((old_quantity * old_average) + notional) / new_quantity
            conn.execute(conn.sql(
                "UPDATE paper_accounts SET cash_balance = cash_balance - ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?"),
                (notional, user_id),
            )
            conn.execute(conn.sql(
                """
                INSERT INTO paper_positions(user_id, symbol, quantity, average_price)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, symbol) DO UPDATE SET
                    quantity = excluded.quantity,
                    average_price = excluded.average_price,
                    updated_at = CURRENT_TIMESTAMP
                """),
                (user_id, symbol, new_quantity, new_average),
            )
        else:
            if not position or float(position["quantity"]) + 1e-9 < quantity:
                raise ValueError("Insufficient paper position quantity for this sell order.")
            remaining = float(position["quantity"]) - quantity
            conn.execute(conn.sql(
                "UPDATE paper_accounts SET cash_balance = cash_balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?"),
                (notional, user_id),
            )
            if remaining <= 1e-9:
                conn.execute(conn.sql("DELETE FROM paper_positions WHERE id = ? AND user_id=?"), (position["id"], user_id))
            else:
                conn.execute(conn.sql(
                    "UPDATE paper_positions SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id=?"),
                    (remaining, position["id"], user_id),
                )

        cursor = conn.execute(conn.sql(
            """
            INSERT INTO paper_orders(user_id, symbol, side, quantity, price, notional, status)
            VALUES (?, ?, ?, ?, ?, ?, 'FILLED') RETURNING id
            """),
            (user_id, symbol, side, quantity, price, notional),
        )
        order_id = int(cursor.fetchone()["id"])
        conn.commit()
    record_audit_event(
        user_id,
        "paper_order_filled",
        "paper_order",
        order_id,
        {"symbol": symbol, "side": side, "quantity": quantity, "price": price},
    )
    return {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": round(quantity, 6),
        "price": round(price, 4),
        "notional": round(notional, 2),
        "status": "FILLED",
    }


def get_paper_account(user_id: int, market_prices: dict[str, float] | None = None) -> dict[str, Any]:
    account = ensure_paper_account(user_id)
    market_prices = {normalize_symbol(k): float(v) for k, v in (market_prices or {}).items()}
    with dao_session() as factory:
        conn = factory.db
        positions = conn.fetchall(conn.sql("SELECT symbol,quantity,average_price FROM paper_positions WHERE user_id=? ORDER BY symbol"), (int(user_id),))
    items = []
    market_value = 0.0
    unrealized = 0.0
    for position in positions:
        symbol, quantity, average_price = (position[key] for key in ("symbol", "quantity", "average_price"))
        current_price = market_prices.get(symbol, float(average_price))
        value = float(quantity) * current_price
        pnl = (current_price - float(average_price)) * float(quantity)
        market_value += value
        unrealized += pnl
        items.append({
            "symbol": symbol,
            "quantity": round(float(quantity), 6),
            "average_price": round(float(average_price), 4),
            "current_price": round(current_price, 4),
            "market_value": round(value, 2),
            "unrealized_pnl": round(pnl, 2),
        })
    total_equity = account["cash_balance"] + market_value
    return {
        **account,
        "market_value": round(market_value, 2),
        "total_equity": round(total_equity, 2),
        "total_return": round(total_equity / account["initial_balance"] - 1.0, 8),
        "unrealized_pnl": round(unrealized, 2),
        "positions": items,
    }


def list_paper_orders(user_id: int, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with dao_session() as factory:
        conn = factory.db
        rows = conn.fetchall(conn.sql(
        """
        SELECT id, symbol, side, quantity, price, notional, status, created_at
        FROM paper_orders WHERE user_id = ? ORDER BY id DESC LIMIT ?
        """),
        (int(user_id), limit),
        )
        return list(rows)


def reset_paper_account(user_id: int, initial_balance: float = DEFAULT_PAPER_BALANCE) -> bool:
    user_id = int(user_id)
    initial_balance = float(initial_balance)
    if not math.isfinite(initial_balance) or initial_balance <= 0:
        raise ValueError("Initial paper balance must be positive.")
    with dao_session() as factory:
        conn = factory.db
        conn.begin_write("paper-account:" + str(user_id))
        conn.execute(conn.sql("DELETE FROM paper_positions WHERE user_id = ?"), (user_id,))
        conn.execute(conn.sql("DELETE FROM paper_orders WHERE user_id = ?"), (user_id,))
        conn.execute(conn.sql(
            """
            INSERT INTO paper_accounts(user_id, initial_balance, cash_balance, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                initial_balance = excluded.initial_balance,
                cash_balance = excluded.cash_balance,
                updated_at = CURRENT_TIMESTAMP
            """),
            (user_id, initial_balance, initial_balance),
        )
        conn.commit()
    record_audit_event(user_id, "paper_account_reset", "paper_account", user_id, {"balance": initial_balance})
    return True
