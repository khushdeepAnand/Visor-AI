# ==========================================================
# IDOR / BOLA cross-user isolation regression suite (v11)
# ==========================================================
#
# Three concurrent registered users (A, B, C) each hold their own browser
# session. A seeds rows in every user-owned store through the same HTTP
# endpoints the frontend uses. B and C then attempt to read, modify, and
# delete A's rows by guessing resource ids across every authenticated
# endpoint category: watchlist, portfolio, paper orders, price alerts,
# saved screens, saved strategies, forward tests, chart layouts, workspace
# layouts, auth sessions, forecast jobs, and saved forecasts.
#
# Every cross-user attempt must be refused (401/403/404) and the acting
# user id must always come from the server-side session, never from the
# request payload. This suite is the CI gate for endpoint-level ownership
# enforcement (IDOR/BOLA).

import pytest
from fastapi.testclient import TestClient

import database
from api.main import app

PASSWORD = "StrongPass9!x"
DOB = "1985-06-15"
ORIGIN = "http://localhost:3000"
# Any 4xx is a refusal. The strategy/forward-test surfaces encode scoped
# not-found/not-owned as 422 (via _feature_error); the others use 401/403/404.
REJECTED = {401, 403, 404, 422}


def _register(client: TestClient, email: str, name: str) -> int:
    response = client.post(
        "/api/v1/auth/register",
        json={"name": name, "email": email, "password": PASSWORD, "date_of_birth": DOB},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == email
    return response.json()["user"]["id"]


def _client() -> TestClient:
    client = TestClient(app)
    client.headers.update({"Origin": ORIGIN})
    return client


@pytest.fixture
def trio(temp_db):
    a, b, c = _client(), _client(), _client()
    a_id = _register(a, "alice.idor@example.com", "Alice")
    b_id = _register(b, "bob.idor@example.com", "Bob")
    c_id = _register(c, "carol.idor@example.com", "Carol")
    assert len({a_id, b_id, c_id}) == 3
    return {"a": a, "b": b, "c": c, "a_id": a_id, "b_id": b_id, "c_id": c_id}


def _watchlist_symbols(response) -> set[str]:
    return {item["symbol"] for item in response.json()["items"]}


def _portfolio_symbols(response) -> set[str]:
    return {item["symbol"] for item in response.json()["holdings"]}


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def test_cross_user_watchlist_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    added = a.post("/api/v1/watchlist", json={"symbol": "RELIANCE"})
    assert added.status_code == 201, added.text
    a_entry = next(item for item in a.get("/api/v1/watchlist").json()["items"] if item["symbol"] == "RELIANCE")

    assert "RELIANCE" not in _watchlist_symbols(b.get("/api/v1/watchlist"))
    assert "RELIANCE" not in _watchlist_symbols(c.get("/api/v1/watchlist"))
    assert b.delete(f"/api/v1/watchlist/{a_entry['id']}").status_code in REJECTED
    assert c.delete(f"/api/v1/watchlist/{a_entry['id']}").status_code in REJECTED
    assert "RELIANCE" in _watchlist_symbols(a.get("/api/v1/watchlist"))

    # Body forgery never redirects ownership.
    forged = b.post("/api/v1/watchlist", json={"symbol": "TCS", "user_id": trio["a_id"]})
    assert forged.status_code == 201, forged.text
    assert "TCS" in _watchlist_symbols(b.get("/api/v1/watchlist"))
    assert "TCS" not in _watchlist_symbols(a.get("/api/v1/watchlist"))


# ---------------------------------------------------------------------------
# Portfolio / paper holdings
# ---------------------------------------------------------------------------

def test_cross_user_portfolio_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    buy = a.post("/api/v1/portfolio", json={"symbol": "RELIANCE", "shares": 10, "buy_price": 2400})
    assert buy.status_code == 201, buy.text
    a_holding = next(item for item in a.get("/api/v1/portfolio").json()["holdings"] if item["symbol"] == "RELIANCE")

    assert "RELIANCE" not in _portfolio_symbols(b.get("/api/v1/portfolio"))
    assert "RELIANCE" not in _portfolio_symbols(c.get("/api/v1/portfolio"))
    for attacker in (b, c):
        assert attacker.put(f"/api/v1/portfolio/{a_holding['id']}", json={"shares": 1, "buy_price": 1}).status_code in REJECTED
        assert attacker.post(f"/api/v1/portfolio/{a_holding['id']}/sell", json={"shares": 1, "sell_price": 2600}).status_code in REJECTED
        assert attacker.delete(f"/api/v1/portfolio/{a_holding['id']}").status_code in REJECTED
    assert "RELIANCE" in _portfolio_symbols(a.get("/api/v1/portfolio"))


# ---------------------------------------------------------------------------
# Paper trades
# ---------------------------------------------------------------------------

def test_cross_user_paper_order_isolation_across_three_users(trio, monkeypatch):
    from services.market_data.demo import DemoIndianProvider
    from services.market_data.manager import MANAGER
    monkeypatch.setattr(MANAGER, "get_quote", lambda symbol, **kwargs: DemoIndianProvider().get_quote(symbol))
    a, b, c = trio["a"], trio["b"], trio["c"]
    order = a.post(
        "/api/v1/paper/orders",
        json={"symbol": "RELIANCE", "side": "BUY", "quantity": 5, "reasoning_notes": "owned by Alice"},
    )
    assert order.status_code == 201, order.text
    a_order_id = order.json()["order_id"]

    for attacker in (b, c):
        assert attacker.delete(f"/api/v1/paper/orders/{a_order_id}").status_code in REJECTED
        journal_ids = {item["order_id"] for item in attacker.get("/api/v1/paper/journal").json()["items"]}
        assert a_order_id not in journal_ids

    forged = b.post(
        "/api/v1/paper/orders",
        json={"symbol": "TCS", "side": "BUY", "quantity": 3, "user_id": trio["a_id"]},
    )
    assert forged.status_code == 201, forged.text
    assert forged.json()["order_id"] != a_order_id


# ---------------------------------------------------------------------------
# Price alerts
# ---------------------------------------------------------------------------

def test_cross_user_price_alert_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    created = a.post("/api/v1/alerts", json={"symbol": "RELIANCE", "condition": "ABOVE", "threshold": 2500.0})
    assert created.status_code == 201, created.text
    a_alert_id = created.json()["id"]

    for attacker in (b, c):
        assert attacker.put(f"/api/v1/alerts/{a_alert_id}", json={"active": False}).status_code in REJECTED
        assert attacker.delete(f"/api/v1/alerts/{a_alert_id}").status_code in REJECTED
        attacker_ids = {item["id"] for item in attacker.get("/api/v1/alerts").json()["items"]}
        assert a_alert_id not in attacker_ids

    # A forged user id in creation payload never redirects the alert owner.
    forged = b.post(
        "/api/v1/alerts",
        json={"symbol": "TCS", "condition": "BELOW", "threshold": 900.0, "user_id": trio["a_id"]},
    )
    assert forged.status_code == 201, forged.text
    b_ids = {item["id"] for item in b.get("/api/v1/alerts").json()["items"]}
    a_ids = {item["id"] for item in a.get("/api/v1/alerts").json()["items"]}
    assert forged.json()["id"] in b_ids and forged.json()["id"] not in a_ids


# ---------------------------------------------------------------------------
# Saved screens (screener)
# ---------------------------------------------------------------------------

def test_cross_user_saved_screen_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    saved = a.post(
        "/api/v1/screener/saved",
        json={"name": "Alice momentum", "symbols": ["RELIANCE"], "filters": [{"field": "rsi_14", "op": "lt", "value": 30}]},
    )
    assert saved.status_code == 201, saved.text
    a_screen_id = saved.json()["id"]

    for attacker in (b, c):
        assert attacker.delete(f"/api/v1/screener/saved/{a_screen_id}").status_code in REJECTED
        assert attacker.post(f"/api/v1/screener/saved/{a_screen_id}/run").status_code in REJECTED
        attacker_ids = {item["id"] for item in attacker.get("/api/v1/screener/saved").json()["screens"]}
        assert a_screen_id not in attacker_ids

    # A forged user id in the create body never redirects the screen owner.
    forged = b.post(
        "/api/v1/screener/saved",
        json={"name": "Bob screen", "symbols": ["TCS"], "filters": [{"field": "rsi_14", "op": "lt", "value": 30}], "user_id": trio["a_id"]},
    )
    assert forged.status_code == 201, forged.text
    assert forged.json()["id"] in {item["id"] for item in b.get("/api/v1/screener/saved").json()["screens"]}


# ---------------------------------------------------------------------------
# Strategy builder
# ---------------------------------------------------------------------------

def test_cross_user_strategy_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    body = {
        "name": "Alice breakout",
        "symbols": ["RELIANCE"],
        "entry": [{"join": "and", "conditions": [{"metric": "close", "operator": "gt", "value": 2400}]}],
        "stop_loss_pct": 5.0,
        "target_pct": 10.0,
    }
    saved = a.post("/api/v1/strategies", json=body)
    assert saved.status_code == 201, saved.text
    a_strategy_id = saved.json()["id"]

    for attacker in (b, c):
        assert attacker.delete(f"/api/v1/strategies/{a_strategy_id}").status_code in REJECTED
        attacker_ids = {item["id"] for item in attacker.get("/api/v1/strategies").json()["strategies"]}
        assert a_strategy_id not in attacker_ids

    # B cannot start a forward test against A's strategy id.
    started = b.post("/api/v1/forward-tests", json={"strategy_id": a_strategy_id, "name": "Bob raid"})
    assert started.status_code in REJECTED


# ---------------------------------------------------------------------------
# Forward tests
# ---------------------------------------------------------------------------

def test_cross_user_forward_test_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    saved = a.post(
        "/api/v1/strategies",
        json={
            "name": "Alice forward",
            "symbols": ["RELIANCE"],
            "entry": [{"join": "and", "conditions": [{"metric": "close", "operator": "gt", "value": 2400}]}],
        },
    )
    assert saved.status_code == 201, saved.text
    a_strategy_id = saved.json()["id"]
    started = a.post("/api/v1/forward-tests", json={"strategy_id": a_strategy_id, "name": "Alice test"})
    assert started.status_code == 201, started.text
    a_test_id = started.json()["id"]

    for attacker in (b, c):
        assert attacker.post(f"/api/v1/forward-tests/{a_test_id}/evaluate", json={"timeframe": "1D", "window": "1y"}).status_code in REJECTED
        assert attacker.post(f"/api/v1/forward-tests/{a_test_id}/stop").status_code in REJECTED
        assert attacker.get(f"/api/v1/forward-tests/{a_test_id}/scorecard").status_code in REJECTED
        attacker_ids = {item["id"] for item in attacker.get("/api/v1/forward-tests").json()["forward_tests"]}
        assert a_test_id not in attacker_ids


# ---------------------------------------------------------------------------
# Chart layouts
# ---------------------------------------------------------------------------

def test_cross_user_chart_layout_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    created = a.post(
        "/api/v1/chart-layouts",
        json={"name": "Alice layout", "symbol": "RELIANCE", "timeframe": "1D", "overlays": {"sma_20": True}},
    )
    assert created.status_code == 201, created.text
    a_layout_id = created.json()["id"]

    for attacker in (b, c):
        assert attacker.get(f"/api/v1/chart-layouts/{a_layout_id}").status_code in REJECTED
        assert attacker.delete(f"/api/v1/chart-layouts/{a_layout_id}").status_code in REJECTED
        attacker_ids = {item["id"] for item in attacker.get("/api/v1/chart-layouts").json()["items"]}
        assert a_layout_id not in attacker_ids


# ---------------------------------------------------------------------------
# Workspace layouts
# ---------------------------------------------------------------------------

def test_cross_user_workspace_layout_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    put = a.put("/api/v1/workspaces/mosaic", json={"layout": {"panels": ["chart"]}})
    assert put.status_code == 200, put.text

    # The same workspace name must not leak A's layout to B or C.
    for attacker in (b, c):
        response = attacker.get("/api/v1/workspaces/mosaic")
        assert response.status_code == 200
        assert response.json()["layout"] in (None, {}, [])
    own = a.get("/api/v1/workspaces/mosaic")
    assert own.json()["layout"] == {"panels": ["chart"]}


# ---------------------------------------------------------------------------
# Auth sessions
# ---------------------------------------------------------------------------

def test_cross_user_session_revocation_isolation(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    a_sessions = a.get("/api/v1/auth/sessions").json()["items"]
    assert a_sessions
    a_session_id = a_sessions[0]["id"]

    for attacker in (b, c):
        n_before = len(attacker.get("/api/v1/auth/sessions").json()["items"])
        assert attacker.delete(f"/api/v1/auth/sessions/{a_session_id}").status_code in REJECTED
        assert len(attacker.get("/api/v1/auth/sessions").json()["items"]) == n_before
    # A's own session still works.
    assert a.get("/api/v1/auth/sessions").status_code == 200


# ---------------------------------------------------------------------------
# Forecast jobs and saved forecasts
# ---------------------------------------------------------------------------

def test_cross_user_forecast_job_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    job = a.post("/api/v1/forecast-jobs", json={"symbol": "RELIANCE", "training_window": "1y"})
    assert job.status_code == 202, job.text
    a_job_id = job.json()["job_id"]

    for attacker in (b, c):
        assert attacker.get(f"/api/v1/forecast-jobs/{a_job_id}").status_code in REJECTED
        assert attacker.delete(f"/api/v1/forecast-jobs/{a_job_id}").status_code in REJECTED


def test_cross_user_saved_forecast_isolation_across_three_users(trio):
    a, b, c = trio["a"], trio["b"], trio["c"]
    prediction_id = database.save_range_forecast(
        trio["a_id"],
        "RELIANCE",
        {"prediction_close": 2500, "low": 2450, "high": 2550, "model": "stockpilot-3", "training_window": "1y", "status": "pending"},
    )

    for attacker in (b, c):
        assert attacker.post(f"/api/v1/predictions/{prediction_id}/settle", json={"actual_price": 2500}).status_code in REJECTED
        assert str(prediction_id) not in attacker.get("/api/v1/predictions/history").text
    assert str(prediction_id) in a.get("/api/v1/predictions/history").text


# ---------------------------------------------------------------------------
# Missing / forged sessions never grant access
# ---------------------------------------------------------------------------

def test_missing_session_is_rejected_everywhere():
    client = TestClient(app)
    assert client.get("/api/v1/watchlist").status_code == 401
    assert client.get("/api/v1/portfolio").status_code == 401
    assert client.get("/api/v1/paper/account").status_code == 401
    assert client.get("/api/v1/predictions/history").status_code == 401
    assert client.get("/api/v1/alerts").status_code == 401
    assert client.get("/api/v1/screener/saved").status_code == 401
    assert client.get("/api/v1/strategies").status_code == 401
    assert client.get("/api/v1/chart-layouts").status_code == 401
    assert client.get("/api/v1/audit").status_code == 401
    assert client.post("/api/v1/predictions/1/settle", json={"actual_price": 1}).status_code == 401


def test_forged_session_cookie_is_rejected():
    forged = TestClient(app)
    forged.cookies.set("stockpilot_session", "forged.invalid.token", path="/")
    assert forged.get("/api/v1/watchlist").status_code == 401
    assert forged.get("/api/v1/portfolio").status_code == 401
    assert forged.get("/api/v1/screener/saved").status_code == 401


def test_third_user_never_sees_first_users_aggregate_rows(trio):
    # Each user's list surfaces contain only their own rows.
    a, b, c = trio["a"], trio["b"], trio["c"]
    a.post("/api/v1/watchlist", json={"symbol": "RELIANCE"})
    a.post("/api/v1/portfolio", json={"symbol": "RELIANCE", "shares": 10, "buy_price": 2400})

    a_watchlist = _watchlist_symbols(a.get("/api/v1/watchlist"))
    assert _watchlist_symbols(b.get("/api/v1/watchlist")).isdisjoint(a_watchlist)
    assert _watchlist_symbols(c.get("/api/v1/watchlist")).isdisjoint(a_watchlist)

    a_portfolio_ids = {item["id"] for item in a.get("/api/v1/portfolio").json()["holdings"]}
    b_portfolio = b.get("/api/v1/portfolio").json()["holdings"]
    c_portfolio = c.get("/api/v1/portfolio").json()["holdings"]
    assert all(item["id"] not in a_portfolio_ids for item in b_portfolio + c_portfolio)
    assert a_watchlist and a_portfolio_ids
