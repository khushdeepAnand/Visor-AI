# ==========================================================
# IDOR / BOLA: cross-user access isolation
# ==========================================================
#
# Two realistic registered users (A and B) each drive their own browser
# session. A creates watchlist entries, holdings, paper trades and forecast
# data; B then attempts to read, update and delete that data by guessing IDs,
# by adding an ownership field to the request body, and by forging or omitting
# a session. Every cross-user attempt must fail with 401/403/404 and the
# acting user must always come from the server-side session, never from the
# request payload.

import pytest
from fastapi.testclient import TestClient

import database
from api.main import app

PASSWORD = "StrongPass9!x"
DOB = "1985-06-15"
ORIGIN = "http://localhost:3000"
REJECTED = {401, 403, 404}


def _register(client: TestClient, email: str, name: str) -> int:
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": PASSWORD, "date_of_birth": DOB},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == email
    return response.json()["user"]["id"]


@pytest.fixture
def pair(temp_db):
    a, b = TestClient(app), TestClient(app)
    for client in (a, b):
        client.headers.update({"Origin": ORIGIN})
    a_id = _register(a, "aldo.cross@example.com", "Aldo")
    b_id = _register(b, "bruno.cross@example.com", "Bruno")
    assert a_id != b_id
    return {"a": a, "b": b, "a_id": a_id, "b_id": b_id}


def _watchlist_symbols(response) -> set[str]:
    return {item["symbol"] for item in response.json()["items"]}


def _portfolio_symbols(response) -> set[str]:
    return {item["symbol"] for item in response.json()["holdings"]}


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------

def test_cross_user_watchlist_isolation(pair):
    a, b = pair["a"], pair["b"]
    added = a.post("/api/v1/watchlist", json={"symbol": "RELIANCE"})
    assert added.status_code == 201, added.text
    a_entry = next(item for item in a.get("/api/v1/watchlist").json()["items"] if item["symbol"] == "RELIANCE")

    # B cannot see A's entry.
    assert "RELIANCE" not in _watchlist_symbols(b.get("/api/v1/watchlist"))

    # B cannot delete A's watchlist entry by guessing its id.
    assert b.delete(f"/api/v1/watchlist/{a_entry['id']}").status_code in REJECTED

    # A forged owner id in the request body is ignored; the item lands in the
    # sender's own list.
    forged = b.post("/api/v1/watchlist", json={"symbol": "TCS", "user_id": pair["a_id"]})
    assert forged.status_code == 201, forged.text
    assert "TCS" in _watchlist_symbols(b.get("/api/v1/watchlist"))
    assert "TCS" not in _watchlist_symbols(a.get("/api/v1/watchlist"))


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------

def test_cross_user_portfolio_isolation(pair):
    a, b = pair["a"], pair["b"]
    buy = a.post("/api/v1/portfolio", json={"symbol": "RELIANCE", "shares": 10, "buy_price": 2400})
    assert buy.status_code == 201, buy.text
    a_holding = next(item for item in a.get("/api/v1/portfolio").json()["holdings"] if item["symbol"] == "RELIANCE")

    # B's portfolio never contains A's holding.
    assert "RELIANCE" not in _portfolio_symbols(b.get("/api/v1/portfolio"))

    # B cannot mutate A's holding by guessing its id.
    assert b.put(f"/api/v1/portfolio/{a_holding['id']}", json={"shares": 1, "buy_price": 1}).status_code in REJECTED
    assert b.post(f"/api/v1/portfolio/{a_holding['id']}/sell", json={"shares": 1, "sell_price": 2600}).status_code in REJECTED
    assert b.delete(f"/api/v1/portfolio/{a_holding['id']}").status_code in REJECTED

    # Forgery in the body never redirects ownership.
    forged = b.post("/api/v1/portfolio", json={"symbol": "TCS", "company": "Tata Motors", "shares": 5, "buy_price": 1000, "user_id": pair["a_id"]})
    assert forged.status_code == 201, forged.text
    assert "TCS" in _portfolio_symbols(b.get("/api/v1/portfolio"))
    assert "TCS" not in _portfolio_symbols(a.get("/api/v1/portfolio"))


# ---------------------------------------------------------------------------
# Paper trades
# ---------------------------------------------------------------------------

def test_cross_user_paper_trade_isolation(pair, monkeypatch):
    from services.market_data.demo import DemoIndianProvider
    from services.market_data.manager import MANAGER
    monkeypatch.setattr(MANAGER, "get_quote", lambda symbol, **kwargs: DemoIndianProvider().get_quote(symbol))
    a, b = pair["a"], pair["b"]
    order = a.post(
        "/api/v1/paper/orders",
        json={"symbol": "RELIANCE", "side": "BUY", "quantity": 5, "reasoning_notes": "owned by Aldo"},
    )
    assert order.status_code == 201, order.text
    a_order_id = order.json()["order_id"]

    # B cannot cancel A's order by guessing its id.
    assert b.delete(f"/api/v1/paper/orders/{a_order_id}").status_code in REJECTED

    # B's journal never contains A's order.
    b_journal_ids = {item["order_id"] for item in b.get("/api/v1/paper/journal").json()["items"]}
    assert a_order_id not in b_journal_ids

    # A forged owner in the body is ignored; the order stays B's.
    forged = b.post(
        "/api/v1/paper/orders",
        json={"symbol": "TCS", "side": "BUY", "quantity": 3, "user_id": pair["a_id"]},
    )
    assert forged.status_code == 201, forged.text
    assert forged.json()["order_id"] != a_order_id


# ---------------------------------------------------------------------------
# Forecasts (jobs and saved predictions)
# ---------------------------------------------------------------------------

def test_cross_user_forecast_job_isolation(pair):
    a, b = pair["a"], pair["b"]
    job = a.post("/api/v1/forecast-jobs", json={"symbol": "RELIANCE", "training_window": "1y"})
    assert job.status_code == 202, job.text
    a_job_id = job.json()["job_id"]

    # B cannot read or delete A's job by guessing its id.
    assert b.get(f"/api/v1/forecast-jobs/{a_job_id}").status_code in REJECTED
    assert b.delete(f"/api/v1/forecast-jobs/{a_job_id}").status_code in REJECTED


def test_cross_user_saved_forecast_isolation(pair):
    a, b = pair["a"], pair["b"]
    # A's saved forecast is inserted through the same storage the API uses; B
    # must neither settle it nor see it in history.
    prediction_id = database.save_range_forecast(
        pair["a_id"],
        "RELIANCE",
        {"prediction_close": 2500, "low": 2450, "high": 2550, "model": "stockpilot-3", "training_window": "1y", "status": "pending"},
    )
    assert b.post(f"/api/v1/predictions/{prediction_id}/settle", json={"actual_price": 2500}).status_code in REJECTED
    assert str(prediction_id) not in b.get("/api/v1/predictions/history").text
    assert str(prediction_id) in a.get("/api/v1/predictions/history").text


# ---------------------------------------------------------------------------
# Forged / missing sessions never grant access
# ---------------------------------------------------------------------------

def test_missing_session_is_rejected_everywhere():
    client = TestClient(app)
    assert client.get("/api/v1/watchlist").status_code == 401
    assert client.get("/api/v1/portfolio").status_code == 401
    assert client.get("/api/v1/paper/account").status_code == 401
    assert client.get("/api/v1/predictions/history").status_code == 401
    assert client.post("/api/v1/predictions/1/settle", json={"actual_price": 1}).status_code == 401


def test_forged_session_cookie_is_rejected():
    forged = TestClient(app)
    forged.cookies.set("stockpilot_session", "forged.invalid.token", path="/")
    assert forged.get("/api/v1/watchlist").status_code == 401
    assert forged.get("/api/v1/portfolio").status_code == 401
