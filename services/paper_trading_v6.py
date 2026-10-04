"""Realistic paper-trading simulator for StockPilot AI v6.

The simulator never routes orders to a broker.  It models market micro-friction,
order triggers, circuit checks, approximate F&O margin, journaling, badges and
leaderboard statistics so simulated results are less flattering than perfect-LTP
fills.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from database import create_tables, get_connection, record_audit_event
from services.market_data.manager import MANAGER
from services.market_data.context import build_market_context
from services.market_data.instruments import CATALOGUE

DEFAULT_BALANCE = 1_000_000.0
ORDER_TYPES = {"MARKET", "LIMIT", "STOP", "TRAILING_STOP", "BRACKET"}
SIDES = {"BUY", "SELL"}
INSTRUMENT_TYPES = {"EQUITY", "FUTURE", "OPTION"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema() -> None:
    create_tables()


def _journal(user_id: int, order_id: int | None, event_type: str, notes: str | None = None, payload: dict | None = None) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO paper_trade_journal(user_id, order_id, event_type, notes, payload_json) VALUES (?, ?, ?, ?, ?)",
        (int(user_id), order_id, event_type, notes, json.dumps(payload or {}, sort_keys=True)),
    )
    conn.commit(); conn.close()


def ensure_account(user_id: int, initial_balance: float = DEFAULT_BALANCE) -> dict[str, Any]:
    ensure_schema()
    user_id = int(user_id); initial_balance = float(initial_balance)
    if initial_balance <= 0:
        raise ValueError("Initial balance must be positive.")
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO paper_accounts(user_id, initial_balance, cash_balance) VALUES (?, ?, ?)",
        (user_id, initial_balance, initial_balance),
    )
    row = conn.execute("SELECT initial_balance, cash_balance, leaderboard_opt_in FROM paper_accounts WHERE user_id=?", (user_id,)).fetchone()
    conn.commit(); conn.close()
    return {"initial_balance": float(row[0]), "cash_balance": float(row[1]), "leaderboard_opt_in": bool(row[2])}


def _fill_price(side: str, quote: dict[str, Any], spread_bps: float, slippage_bps: float) -> float:
    ltp = float(quote["price"])
    half_spread = ltp * max(0.0, float(spread_bps)) / 20_000.0
    slippage = ltp * max(0.0, float(slippage_bps)) / 10_000.0
    return ltp + half_spread + slippage if side == "BUY" else max(0.01, ltp - half_spread - slippage)


def _circuit_bounds(previous_close: float | None, circuit_limit_pct: float) -> tuple[float | None, float | None]:
    if not previous_close or previous_close <= 0:
        return None, None
    pct = float(circuit_limit_pct) / 100.0
    return previous_close * (1 - pct), previous_close * (1 + pct)


def estimate_margin(*, instrument_type: str, quantity: float, price: float, lot_size: float = 1.0, option_side: str | None = None) -> float:
    """Conservative SPAN/exposure-style approximation, not an exchange margin quote."""
    notional = float(quantity) * float(price) * max(1.0, float(lot_size))
    kind = instrument_type.upper()
    if kind == "EQUITY":
        return notional
    if kind == "FUTURE":
        return notional * 0.18
    if kind == "OPTION":
        # Buying premium is fully funded; short option simulation reserves a
        # conservative notional fraction because exact SPAN requires exchange files.
        return notional if str(option_side or "BUY").upper() == "BUY" else notional * 0.22
    raise ValueError("Unknown instrument type.")


def _triggered(order_type: str, side: str, market_price: float, *, limit_price: float | None, stop_price: float | None) -> bool:
    if order_type in {"MARKET", "BRACKET"}:
        return True
    if order_type == "LIMIT":
        if limit_price is None or limit_price <= 0:
            raise ValueError("LIMIT orders require a positive limit_price.")
        return market_price <= limit_price if side == "BUY" else market_price >= limit_price
    if order_type in {"STOP", "TRAILING_STOP"}:
        if stop_price is None or stop_price <= 0:
            raise ValueError(f"{order_type} orders require a positive stop_price.")
        return market_price >= stop_price if side == "BUY" else market_price <= stop_price
    return False


def place_order(
    *,
    user_id: int,
    symbol: str,
    side: str,
    quantity: float,
    order_type: str = "MARKET",
    limit_price: float | None = None,
    stop_price: float | None = None,
    trail_amount: float | None = None,
    target_price: float | None = None,
    spread_bps: float = 5.0,
    slippage_bps: float = 2.0,
    reasoning_notes: str | None = None,
    linked_alert_id: int | None = None,
    instrument_type: str = "EQUITY",
    expiry: str | None = None,
    strike: float | None = None,
    option_type: str | None = None,
    lot_size: float = 1.0,
    circuit_limit_pct: float = 20.0,
    market_quote: dict[str, Any] | None = None,
    timeframe: str = "1D",
) -> dict[str, Any]:
    ensure_schema(); ensure_account(user_id)
    user_id = int(user_id); symbol = MANAGER.normalize_symbol(symbol)
    side = str(side).upper(); order_type = str(order_type).upper(); instrument_type = str(instrument_type).upper()
    quantity = float(quantity)
    if side not in SIDES: raise ValueError("side must be BUY or SELL.")
    if order_type not in ORDER_TYPES: raise ValueError(f"order_type must be one of {sorted(ORDER_TYPES)}")
    if instrument_type not in INSTRUMENT_TYPES: raise ValueError(f"instrument_type must be one of {sorted(INSTRUMENT_TYPES)}")
    if quantity <= 0: raise ValueError("quantity must be positive.")
    if order_type == "TRAILING_STOP" and (trail_amount is None or float(trail_amount) <= 0):
        raise ValueError("TRAILING_STOP requires trail_amount > 0.")
    if order_type == "BRACKET":
        if stop_price is None or target_price is None or float(stop_price) <= 0 or float(target_price) <= 0:
            raise ValueError("BRACKET orders require positive stop_price and target_price.")

    quote = market_quote or MANAGER.get_quote(symbol, timeframe=timeframe).to_dict()
    context = quote.get("context")
    if not isinstance(context, dict):
        context = build_market_context(
            requested_symbol=symbol,
            instrument=CATALOGUE.resolve(symbol),
            provider="supplied_market_quote",
            credential_mode="supplied",
            timeframe=timeframe,
            as_of=str(quote.get("timestamp")) if quote.get("timestamp") else None,
            is_live=False,
            is_stale=bool(quote.get("is_stale", False)),
            fallback_used=False,
            fallback_reason=None,
        )
    market_price = float(quote["price"])
    previous_close = quote.get("previous_close")
    lower_circuit, upper_circuit = _circuit_bounds(float(previous_close) if previous_close else None, circuit_limit_pct)
    if (
        lower_circuit is not None
        and upper_circuit is not None
        and not (lower_circuit <= market_price <= upper_circuit)
    ):
        raise ValueError("Order rejected: current simulated price is outside the configured circuit band.")

    should_fill = _triggered(order_type, side, market_price, limit_price=limit_price, stop_price=stop_price)
    fill_price = _fill_price(side, quote, spread_bps, slippage_bps) if should_fill else market_price
    margin = estimate_margin(instrument_type=instrument_type, quantity=quantity, price=fill_price, lot_size=lot_size, option_side=side)
    status = "FILLED" if should_fill else "OPEN"
    notional = quantity * fill_price * max(1.0, float(lot_size))

    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        account = conn.execute("SELECT cash_balance FROM paper_accounts WHERE user_id=?", (user_id,)).fetchone()
        cash = float(account[0])
        contract_symbol = symbol if instrument_type == "EQUITY" else f"{symbol}:{instrument_type}:{expiry or '-'}:{strike or '-'}:{option_type or '-'}"
        position = conn.execute("SELECT id, quantity, average_price, lot_size FROM paper_positions WHERE user_id=? AND symbol=?", (user_id, contract_symbol)).fetchone()
        if position and instrument_type != "EQUITY" and abs(float(position[3] or 1.0) - float(lot_size)) > 1e-9:
            raise ValueError("lot_size must match the existing paper position for this contract.")

        if should_fill:
            if side == "BUY":
                debit = notional if instrument_type in {"EQUITY", "OPTION"} else margin
                if debit > cash + 1e-9: raise ValueError("Insufficient paper cash/margin for this order.")
                old_qty = float(position[1]) if position else 0.0; old_avg = float(position[2]) if position else 0.0
                new_qty = old_qty + quantity
                new_avg = ((old_qty * old_avg) + quantity * fill_price) / new_qty
                conn.execute("UPDATE paper_accounts SET cash_balance=cash_balance-?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (debit, user_id))
                conn.execute(
                    """INSERT INTO paper_positions(user_id,symbol,quantity,average_price,lot_size) VALUES(?,?,?,?,?)
                    ON CONFLICT(user_id,symbol) DO UPDATE SET quantity=excluded.quantity,average_price=excluded.average_price,lot_size=excluded.lot_size,updated_at=CURRENT_TIMESTAMP""",
                    (user_id, contract_symbol, new_qty, new_avg, float(lot_size)),
                )
                realized = 0.0
            else:
                if not position or float(position[1]) + 1e-9 < quantity:
                    raise ValueError("Insufficient paper position quantity for this sell order.")
                old_qty = float(position[1]); old_avg = float(position[2]); remaining = old_qty - quantity
                realized = (fill_price - old_avg) * quantity * max(1.0, float(lot_size))
                credit = notional if instrument_type in {"EQUITY", "OPTION"} else margin + realized
                conn.execute("UPDATE paper_accounts SET cash_balance=cash_balance+?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (credit, user_id))
                if remaining <= 1e-9:
                    conn.execute("DELETE FROM paper_positions WHERE id=?", (position[0],))
                else:
                    conn.execute("UPDATE paper_positions SET quantity=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (remaining, position[0]))
        else:
            realized = 0.0

        cur = conn.execute(
            """INSERT INTO paper_orders(user_id,symbol,side,quantity,price,notional,status,order_type,limit_price,stop_price,trail_amount,spread_bps,slippage_bps,reasoning_notes,linked_alert_id,updated_at,filled_at,instrument_type,expiry,strike,option_type,margin_required,realized_pnl,target_price,lot_size)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (user_id, contract_symbol, side, quantity, fill_price, notional, status, order_type, limit_price, stop_price, trail_amount, spread_bps, slippage_bps, reasoning_notes, linked_alert_id, _now(), _now() if should_fill else None, instrument_type, expiry, strike, option_type, margin, realized, target_price, float(lot_size)),
        )
        order_id = int(cur.lastrowid)
        conn.commit()
    except Exception:
        conn.rollback(); conn.close(); raise
    conn.close()

    payload = {"symbol": symbol, "side": side, "quantity": quantity, "order_type": order_type, "status": status, "fill_price": fill_price, "market_price": market_price, "spread_bps": spread_bps, "slippage_bps": slippage_bps, "margin_required": margin, "stop_price": stop_price, "target_price": target_price, "context": context}
    bracket_children: list[int] = []
    if order_type == "BRACKET" and should_fill:
        if stop_price is None or target_price is None:
            raise AssertionError("validated bracket prices are missing")
        exit_side = "SELL" if side == "BUY" else "BUY"
        oco_group = f"BRACKET-{user_id}-{order_id}"
        child_rows = [
            (exit_side, "STOP", None, float(stop_price), f"Bracket stop for #{order_id}"),
            (exit_side, "LIMIT", float(target_price), None, f"Bracket target for #{order_id}"),
        ]
        conn = get_connection()
        for child_side, child_type, child_limit, child_stop, child_note in child_rows:
            trigger_price = child_limit or child_stop or market_price
            child_notional = quantity * float(trigger_price) * max(1.0, float(lot_size))
            cur = conn.execute(
                """INSERT INTO paper_orders(user_id,symbol,side,quantity,price,notional,status,order_type,limit_price,stop_price,trail_amount,spread_bps,slippage_bps,reasoning_notes,linked_alert_id,updated_at,instrument_type,expiry,strike,option_type,margin_required,realized_pnl,target_price,parent_order_id,oco_group,lot_size)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, contract_symbol, child_side, quantity, market_price, child_notional, "OPEN", child_type, child_limit, child_stop, None, spread_bps, slippage_bps, child_note, linked_alert_id, _now(), instrument_type, expiry, strike, option_type, margin, 0.0, target_price, order_id, oco_group, float(lot_size)),
            )
            bracket_children.append(int(cur.lastrowid))
        conn.commit(); conn.close()
        payload["bracket_children"] = bracket_children
    _journal(user_id, order_id, "PLACED" if not should_fill else "PLACED_AND_FILLED", reasoning_notes, payload)
    record_audit_event(user_id, "paper_order_" + status.lower(), "paper_order", order_id, payload)
    _award_badges(user_id)
    return {"order_id": order_id, **payload, "notional": round(notional, 2), "realized_pnl": round(realized, 2), "simulation_notice": "Paper trade only. No broker order was sent."}


def process_open_orders(user_id: int, symbol: str | None = None, market_quote: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Evaluate open trigger orders. Returns orders that became fillable.

    For accounting safety this function cancels/replaces each triggered database
    row by delegating the actual fill through ``place_order`` and marks the
    original as TRIGGERED. This preserves an immutable lifecycle trail.
    """
    ensure_schema()
    conn = get_connection()
    query = "SELECT id,symbol,side,quantity,order_type,limit_price,stop_price,trail_amount,spread_bps,slippage_bps,reasoning_notes,instrument_type,expiry,strike,option_type,oco_group,lot_size FROM paper_orders WHERE user_id=? AND status='OPEN'"
    params: list[Any] = [int(user_id)]
    if symbol:
        query += " AND symbol LIKE ?"; params.append(MANAGER.normalize_symbol(symbol) + "%")
    rows = conn.execute(query, tuple(params)).fetchall(); conn.close()
    filled = []
    for row in rows:
        order_id, contract, side, qty, order_type, limit_price, stop_price, trail_amount, spread_bps, slippage_bps, notes, instrument_type, expiry, strike, option_type, oco_group, lot_size = row
        base_symbol = str(contract).split(":", 1)[0]
        quote = market_quote or MANAGER.get_quote(base_symbol).to_dict()
        current = float(quote["price"])
        local_stop = stop_price
        if order_type == "TRAILING_STOP" and trail_amount:
            # Conservative single-step trailing update using current market price.
            candidate = current - float(trail_amount) if side == "SELL" else current + float(trail_amount)
            local_stop = max(float(stop_price or candidate), candidate) if side == "SELL" else min(float(stop_price or candidate), candidate)
            if local_stop != stop_price:
                conn = get_connection(); conn.execute("UPDATE paper_orders SET stop_price=?,updated_at=? WHERE id=? AND user_id=?", (local_stop, _now(), order_id, int(user_id))); conn.commit(); conn.close()
        if not _triggered(order_type, side, current, limit_price=limit_price, stop_price=local_stop):
            continue
        conn = get_connection()
        claimed = conn.execute(
            "UPDATE paper_orders SET status='TRIGGERED',updated_at=? WHERE id=? AND user_id=? AND status='OPEN'",
            (_now(), order_id, int(user_id)),
        ).rowcount
        conn.commit(); conn.close()
        if claimed != 1:
            continue
        _journal(user_id, order_id, "TRIGGERED", payload={"market_price": current})
        filled.append(place_order(user_id=user_id, symbol=base_symbol, side=side, quantity=qty, order_type="MARKET", spread_bps=spread_bps, slippage_bps=slippage_bps, reasoning_notes=f"Triggered from order #{order_id}. {notes or ''}".strip(), instrument_type=instrument_type, expiry=expiry, strike=strike, option_type=option_type, lot_size=float(lot_size or 1.0), market_quote=quote))
        if oco_group:
            conn = get_connection()
            siblings = conn.execute("SELECT id FROM paper_orders WHERE user_id=? AND oco_group=? AND status='OPEN' AND id<>?", (int(user_id), oco_group, order_id)).fetchall()
            conn.execute("UPDATE paper_orders SET status='CANCELLED',cancelled_at=?,updated_at=? WHERE user_id=? AND oco_group=? AND status='OPEN' AND id<>?", (_now(), _now(), int(user_id), oco_group, order_id))
            conn.commit(); conn.close()
            for sibling in siblings:
                _journal(user_id, int(sibling[0]), "OCO_CANCELLED", payload={"triggered_sibling": order_id})
    return filled


def cancel_order(user_id: int, order_id: int) -> bool:
    ensure_schema(); conn = get_connection()
    cur = conn.execute("UPDATE paper_orders SET status='CANCELLED',cancelled_at=?,updated_at=? WHERE id=? AND user_id=? AND status='OPEN'", (_now(), _now(), int(order_id), int(user_id)))
    conn.commit(); changed = cur.rowcount > 0; conn.close()
    if changed: _journal(user_id, order_id, "CANCELLED")
    return changed


def journal(user_id: int, limit: int = 200) -> list[dict[str, Any]]:
    ensure_schema(); conn = get_connection()
    rows = conn.execute("SELECT id,order_id,event_type,notes,payload_json,created_at FROM paper_trade_journal WHERE user_id=? ORDER BY id DESC LIMIT ?", (int(user_id), max(1,min(int(limit),1000)))).fetchall(); conn.close()
    return [{"id":r[0],"order_id":r[1],"event_type":r[2],"notes":r[3],"payload":json.loads(r[4] or "{}"),"created_at":r[5]} for r in rows]


def _award_badges(user_id: int) -> None:
    conn = get_connection()
    count = int(conn.execute("SELECT COUNT(*) FROM paper_orders WHERE user_id=? AND status='FILLED'", (int(user_id),)).fetchone()[0])
    option_count = int(conn.execute("SELECT COUNT(*) FROM paper_orders WHERE user_id=? AND status='FILLED' AND instrument_type='OPTION'", (int(user_id),)).fetchone()[0])
    profitable = int(conn.execute("SELECT COUNT(*) FROM paper_orders WHERE user_id=? AND status='FILLED' AND realized_pnl>0", (int(user_id),)).fetchone()[0])
    badges = []
    if count >= 1: badges.append(("FIRST_TRADE", "First paper trade"))
    if count >= 5: badges.append(("FIVE_TRADES", "Five-trade streak"))
    if option_count >= 1: badges.append(("FIRST_OPTION", "First options paper trade"))
    if profitable >= 5: badges.append(("FIVE_PROFITABLE_EXITS", "Five profitable exits"))
    for key,title in badges:
        conn.execute("INSERT OR IGNORE INTO paper_badges(user_id,badge_key,title) VALUES(?,?,?)", (int(user_id), key, title))
    conn.commit(); conn.close()


def badges(user_id: int) -> list[dict[str, Any]]:
    ensure_schema(); _award_badges(user_id); conn=get_connection()
    rows=conn.execute("SELECT badge_key,title,earned_at FROM paper_badges WHERE user_id=? ORDER BY earned_at",(int(user_id),)).fetchall(); conn.close()
    return [{"key":r[0],"title":r[1],"earned_at":r[2]} for r in rows]


def set_leaderboard_opt_in(user_id: int, enabled: bool) -> bool:
    """Set explicit leaderboard visibility for one paper account."""
    ensure_schema(); ensure_account(user_id)
    conn = get_connection()
    cur = conn.execute(
        "UPDATE paper_accounts SET leaderboard_opt_in=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
        (1 if enabled else 0, int(user_id)),
    )
    conn.commit(); changed = cur.rowcount > 0; conn.close()
    if changed:
        _journal(user_id, None, "LEADERBOARD_OPT_IN" if enabled else "LEADERBOARD_OPT_OUT")
    return changed


def leaderboard(limit: int = 25) -> list[dict[str, Any]]:
    """Return only paper accounts that explicitly opted into public ranking."""
    ensure_schema(); conn=get_connection()
    rows=conn.execute("""SELECT u.id,u.name,a.initial_balance,a.cash_balance,
        COALESCE(SUM(p.quantity*p.average_price*COALESCE(p.lot_size,1)),0) AS position_cost
        FROM paper_accounts a JOIN users u ON u.id=a.user_id LEFT JOIN paper_positions p ON p.user_id=a.user_id
        WHERE a.leaderboard_opt_in=1
        GROUP BY u.id,u.name,a.initial_balance,a.cash_balance""").fetchall(); conn.close()
    items=[]
    for uid,name,initial,cash,pos_cost in rows:
        equity=float(cash)+float(pos_cost); ret=equity/float(initial)-1 if initial else 0
        items.append({"user_id":uid,"name":name,"equity":round(equity,2),"return_pct":round(ret*100,4)})
    items.sort(key=lambda x:x["return_pct"], reverse=True)
    return items[:max(1,min(int(limit),100))]


def weekly_challenges() -> list[dict[str, Any]]:
    return [
        {"key":"NIFTY_DROP_3","title":"NIFTY shock replay","scenario":"NIFTY drops 3% in one session. Choose HOLD, BUY_DIP or EXIT and compare the simulated next-session outcome.","choices":["HOLD","BUY_DIP","EXIT"]},
        {"key":"VOLATILITY_SPIKE","title":"Volatility spike","scenario":"India VIX jumps while your equity position is profitable. Choose REDUCE, HOLD or HEDGE.","choices":["REDUCE","HOLD","HEDGE"]},
    ]
