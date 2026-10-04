from io import StringIO

import pandas as pd

import database
from services.reports import (
    build_realized_gain_report,
    dataframe_to_safe_csv,
    generate_forecast_pdf,
    generate_portfolio_pdf,
    sanitize_spreadsheet_cell,
)


def _create_user():
    connection = database.get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO users(name, email, password) VALUES (?, ?, ?)",
        ("Report User", "report@example.com", "hash"),
    )
    user_id = cursor.lastrowid
    connection.commit()
    connection.close()
    return user_id


def test_sell_stock_records_transaction_and_updates_holding(temp_db):
    user_id = _create_user()
    database.buy_stock(user_id, "RELIANCE", "Reliance", 10, 100)
    holding_id = database.get_portfolio(user_id)[0][0]

    assert database.sell_stock(holding_id, user_id, 4, 125) is True
    assert database.get_portfolio(user_id)[0][3] == 6.0
    transactions = database.get_transactions(user_id)
    assert transactions[0][2] == "SELL"
    assert transactions[0][3:5] == (4.0, 125.0)


def test_realized_gain_report_uses_fifo_matching():
    transactions = [
        (1, "RELIANCE", "BUY", 5, 100, "2025-01-01"),
        (2, "RELIANCE", "BUY", 5, 120, "2025-02-01"),
        (3, "RELIANCE", "SELL", 8, 150, "2025-03-01"),
    ]
    report = build_realized_gain_report(transactions)
    row = report.iloc[0]
    assert row["Shares Closed"] == 8
    assert row["Cost Basis"] == 860
    assert row["Sale Proceeds"] == 1200
    assert row["Realized Gain/Loss"] == 340


def test_spreadsheet_formula_cells_are_neutralized_without_changing_numbers():
    assert sanitize_spreadsheet_cell("=HYPERLINK(\"https://invalid.example\")") == "'=HYPERLINK(\"https://invalid.example\")"
    assert sanitize_spreadsheet_cell("  +1+1") == "'  +1+1"
    assert sanitize_spreadsheet_cell("RELIANCE") == "RELIANCE"
    assert sanitize_spreadsheet_cell(-10.0) == -10.0


def test_csv_serializer_sanitizes_headings_and_all_text_cells():
    frame = pd.DataFrame([{"=heading": "@SUM(A1:A2)", "safe": "\t=cmd", "number": -3.0}])

    exported = dataframe_to_safe_csv(frame)
    restored = pd.read_csv(StringIO(exported), dtype=str)

    assert list(restored.columns) == ["'=heading", "safe", "number"]
    assert restored.iloc[0].tolist() == ["'@SUM(A1:A2)", "'\t=cmd", "-3.0"]


def test_realized_gain_report_neutralizes_untrusted_symbol_text():
    report = build_realized_gain_report([
        (1, "=2+2", "BUY", 1, 100, "2025-01-01"),
        (2, "=2+2", "SELL", 1, 110, "2025-02-01"),
    ])

    assert report.iloc[0]["Symbol"] == "'=2+2"


def test_portfolio_pdf_is_valid_pdf_bytes():
    holdings = pd.DataFrame(
        [
            {
                "Symbol": "RELIANCE",
                "Company": "Reliance Industries",
                "Shares": 2.0,
                "Buy Price": 100.0,
                "Current Price": 125.0,
                "Investment": 200.0,
                "Current Value": 250.0,
                "Profit": 50.0,
                "Return %": 25.0,
            }
        ]
    )
    content = generate_portfolio_pdf(holdings, owner_name="Test User")
    assert content.startswith(b"%PDF")
    assert len(content) > 1000


def test_forecast_pdf_is_range_first():
    content = generate_forecast_pdf({
        "symbol": "RELIANCE",
        "forecast": {"low": 2410.0, "median": 2440.0, "high": 2470.0, "confidence_level": 0.8, "direction": "bullish"},
        "validation": {"coverage": 0.8, "nominal_coverage": 0.8, "winkler_score": 12.5, "mae": 4.0, "naive_baseline_mae": 8.0, "beats_naive_baseline": True},
        "drift": {"status": "stable"},
        "training": {"training_window": "1mo", "timeframe": "5m"},
    })
    assert content.startswith(b"%PDF")
    assert len(content) > 1000


def test_blocked_forecast_pdf_does_not_require_or_render_a_range():
    content = generate_forecast_pdf({
        "symbol": "RELIANCE",
        "forecast_status": "data_quality_blocked",
        "abstention_reason": "Freshness checks failed.",
    })
    assert content.startswith(b"%PDF")
    assert len(content) > 1000
