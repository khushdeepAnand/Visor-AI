"""Portfolio PDF and transaction-based tax-estimate helpers."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from io import BytesIO
from typing import Iterable

import pandas as pd


SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def sanitize_spreadsheet_cell(value: object) -> object:
    """Neutralize text that spreadsheet applications may execute as a formula."""

    if not isinstance(value, str):
        return value
    significant = value.lstrip(" \t\r\n")
    if significant.startswith(SPREADSHEET_FORMULA_PREFIXES) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def dataframe_to_safe_csv(frame: pd.DataFrame, *, index: bool = False) -> str:
    """Serialize a DataFrame after sanitizing data cells and column headings."""

    safe = frame.map(sanitize_spreadsheet_cell)
    safe.columns = [sanitize_spreadsheet_cell(str(column)) for column in safe.columns]
    return safe.to_csv(index=index)


def build_realized_gain_report(transactions: Iterable[tuple]) -> pd.DataFrame:
    """Estimate realized gains using FIFO matching of existing BUY/SELL rows.

    The transaction schema does not store tax lots, fees, or jurisdictional tax
    rules, so this deliberately produces a personal-reference estimate only.
    """

    parsed = []
    for row in transactions:
        if len(row) < 6:
            continue
        transaction_id, symbol, transaction_type, shares, price, date = row[:6]
        try:
            shares = float(shares)
            price = float(price)
        except (TypeError, ValueError):
            continue
        if shares <= 0 or price < 0:
            continue
        parsed.append(
            {
                "id": transaction_id,
                "symbol": sanitize_spreadsheet_cell(str(symbol or "").strip().upper()),
                "type": str(transaction_type or "").strip().upper(),
                "shares": shares,
                "price": price,
                "date": pd.to_datetime(date, errors="coerce"),
            }
        )

    parsed.sort(
        key=lambda item: (
            pd.Timestamp.min if pd.isna(item["date"]) else item["date"],
            item["id"],
        )
    )

    lots: dict[str, deque] = defaultdict(deque)
    realized_rows = []

    for transaction in parsed:
        symbol = transaction["symbol"]
        if not symbol:
            continue
        if transaction["type"] == "BUY":
            lots[symbol].append(
                {
                    "shares": transaction["shares"],
                    "price": transaction["price"],
                    "date": transaction["date"],
                }
            )
            continue
        if transaction["type"] != "SELL":
            continue

        remaining = transaction["shares"]
        matched_shares = 0.0
        cost_basis = 0.0
        earliest_buy_date = None

        while remaining > 1e-12 and lots[symbol]:
            lot = lots[symbol][0]
            matched = min(remaining, lot["shares"])
            matched_shares += matched
            cost_basis += matched * lot["price"]
            if earliest_buy_date is None:
                earliest_buy_date = lot["date"]
            lot["shares"] -= matched
            remaining -= matched
            if lot["shares"] <= 1e-12:
                lots[symbol].popleft()

        if matched_shares <= 0:
            continue

        proceeds = matched_shares * transaction["price"]
        gain = proceeds - cost_basis
        return_percent = gain / cost_basis * 100 if cost_basis else 0.0
        holding_days = None
        if earliest_buy_date is not None and not pd.isna(earliest_buy_date) and not pd.isna(transaction["date"]):
            holding_days = max(0, (transaction["date"] - earliest_buy_date).days)

        realized_rows.append(
            {
                "Symbol": symbol,
                "Buy Date": earliest_buy_date,
                "Sell Date": transaction["date"],
                "Shares Closed": matched_shares,
                "Cost Basis": cost_basis,
                "Sale Proceeds": proceeds,
                "Realized Gain/Loss": gain,
                "Return %": return_percent,
                "Holding Days": holding_days,
                "Unmatched Sell Shares": max(0.0, remaining),
            }
        )

    return pd.DataFrame(realized_rows)


def generate_portfolio_pdf(
    holdings: pd.DataFrame,
    *,
    owner_name: str = "StockPilot AI User",
) -> bytes:
    """Generate a compact professional portfolio summary as PDF bytes."""

    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "PDF export requires reportlab. Install the declared project requirements."
        ) from error

    if holdings is None or holdings.empty:
        raise ValueError("At least one portfolio holding is required for PDF export.")

    frame = holdings.copy()
    invested = float(pd.to_numeric(frame["Investment"], errors="coerce").fillna(0).sum())
    current_value = float(pd.to_numeric(frame["Current Value"], errors="coerce").fillna(0).sum())
    total_profit = current_value - invested
    total_return = total_profit / invested * 100 if invested else 0.0
    frame["Allocation %"] = (
        pd.to_numeric(frame["Current Value"], errors="coerce").fillna(0)
        / current_value * 100
        if current_value
        else 0.0
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="StockPilot AI Portfolio Summary",
        author="StockPilot AI",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "StockPilotTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=24,
        spaceAfter=8,
    )

    story = [
        Paragraph("StockPilot AI — Portfolio Summary", title_style),
        Paragraph(
            f"Owner: {owner_name} &nbsp;&nbsp;|&nbsp;&nbsp; Generated: "
            f"{datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}",
            styles["Normal"],
        ),
        Spacer(1, 8),
    ]

    summary_data = [
        ["Invested", "Current Value", "Profit / Loss", "Total Return", "Holdings"],
        [
            f"{invested:,.2f}",
            f"{current_value:,.2f}",
            f"{total_profit:+,.2f}",
            f"{total_return:+.2f}%",
            str(len(frame)),
        ],
    ]
    summary = Table(summary_data, colWidths=[48 * mm] * 5)
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18233A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9BA7B8")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F4F7FB")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([summary, Spacer(1, 12), Paragraph("Holdings and Allocation", styles["Heading2"])])

    headings = [
        "Symbol", "Company", "Shares", "Buy Price", "Current Price",
        "Investment", "Current Value", "Profit", "Return %", "Allocation %",
    ]
    table_data = [headings]
    for _, row in frame.sort_values("Current Value", ascending=False).iterrows():
        table_data.append(
            [
                str(row.get("Symbol", "")),
                str(row.get("Company", ""))[:28],
                f"{float(row.get('Shares', 0)):,.2f}",
                f"{float(row.get('Buy Price', 0)):,.2f}",
                f"{float(row.get('Current Price', 0)):,.2f}",
                f"{float(row.get('Investment', 0)):,.2f}",
                f"{float(row.get('Current Value', 0)):,.2f}",
                f"{float(row.get('Profit', 0)):+,.2f}",
                f"{float(row.get('Return %', 0)):+.2f}%",
                f"{float(row.get('Allocation %', 0)):.2f}%",
            ]
        )

    holdings_table = Table(
        table_data,
        repeatRows=1,
        colWidths=[20 * mm, 42 * mm, 18 * mm, 23 * mm, 25 * mm, 25 * mm, 27 * mm, 24 * mm, 22 * mm, 24 * mm],
    )
    holdings_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18233A")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C0CE")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FB")]),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            holdings_table,
            Spacer(1, 10),
            Paragraph(
                "Market prices may be delayed or use cached fallbacks. This report is for "
                "personal research and record-keeping, not investment or tax advice.",
                styles["Italic"],
            ),
        ]
    )

    document.build(story)
    return buffer.getvalue()


def generate_forecast_pdf(
    result: dict,
    *,
    owner_name: str = "StockPilot AI User",
) -> bytes:
    """Render a range-first forecast report.

    The report deliberately has no naked ``predicted_price`` field: the primary
    forecast surface is always low / median / high with the requested coverage.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as error:  # pragma: no cover - dependency guard
        raise RuntimeError("PDF export requires reportlab. Install requirements.txt.") from error

    forecast = result.get("forecast") or {}
    required = ("low", "median", "high", "confidence_level")
    forecast_status = str(result.get("forecast_status") or "available")
    blocked = forecast_status in {"abstained", "drift_blocked", "data_quality_blocked"}
    if not blocked and any(key not in forecast for key in required):
        raise ValueError("Forecast report requires low, median, high and confidence_level.")

    symbol = str(result.get("symbol") or "UNKNOWN")
    low = float(forecast.get("low") or 0.0)
    median = float(forecast.get("median") or 0.0)
    high = float(forecast.get("high") or 0.0)
    confidence = float(forecast.get("confidence_level") or 0.0)
    validation = result.get("validation") or {}
    drift = result.get("drift") or {}
    training = result.get("training") or {}

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"StockPilot AI Range Forecast — {symbol}",
        author="StockPilot AI",
    )
    styles = getSampleStyleSheet()
    accent = colors.HexColor("#0B6E63")
    title_style = ParagraphStyle("ForecastTitle", parent=styles["Title"], fontSize=20, leading=24, textColor=colors.HexColor("#152033"))
    range_style = ParagraphStyle("ForecastRange", parent=styles["Heading1"], fontSize=17, leading=22, textColor=accent, spaceAfter=6)

    story = [
        Paragraph(f"StockPilot AI — {symbol} Range Forecast", title_style),
        Paragraph(
            f"Owner: {owner_name} &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}",
            styles["Normal"],
        ),
        Spacer(1, 14),
    ]
    if blocked:
        story.extend([
            Paragraph("No numerical corridor released", range_style),
            Paragraph(str(result.get("abstention_reason") or f"Trust gate: {forecast_status}."), styles["Normal"]),
            Spacer(1, 16),
        ])
    else:
        story.extend([
            Paragraph(f"{int(round(confidence * 100))}% interval: ₹{low:,.2f} – ₹{high:,.2f}", range_style),
            Paragraph(f"Model median inside the interval: ₹{median:,.2f}", styles["Normal"]),
            Spacer(1, 12),
        ])
        overview = [
            ["Low", "Median", "High", "Confidence", "Direction"],
            [f"₹{low:,.2f}", f"₹{median:,.2f}", f"₹{high:,.2f}", f"{confidence * 100:.0f}%", str(forecast.get("direction") or "—")],
        ]
        table = Table(overview, colWidths=[32 * mm, 32 * mm, 32 * mm, 32 * mm, 38 * mm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18233A")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9BA7B8")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F4F7FB")),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([table, Spacer(1, 16)])

    details = [
        ["Validation metric", "Value"],
        ["Empirical interval coverage", f"{float(validation.get('empirical_coverage', validation.get('coverage', 0.0))) * 100:.2f}%" if validation.get("empirical_coverage", validation.get("coverage")) is not None else "—"],
        ["Nominal coverage", f"{float(validation.get('nominal_coverage', confidence)) * 100:.2f}%"],
        ["Winkler score", str(validation.get("winkler_score", "—"))],
        ["Model MAE", str(validation.get("mae", "—"))],
        ["Naive baseline MAE", str(validation.get("naive_baseline_mae", "—"))],
        ["Beats naive baseline", str(validation.get("beats_naive_baseline", "—"))],
        ["Drift status", str(drift.get("status", "—"))],
        ["Training window", str(training.get("training_window", training.get("window", "—")))],
        ["Timeframe", str(training.get("timeframe", "—"))],
    ]
    detail_table = Table(details, colWidths=[70 * mm, 95 * mm], repeatRows=1)
    detail_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF6")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B7C0CE")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    if validation or drift or training:
        # A report generated for a normal user carries no diagnostic blocks at
        # all, so the section is omitted rather than printed as empty rows.
        story.extend([
            Paragraph("Validation and monitoring", styles["Heading2"]),
            detail_table,
            Spacer(1, 14),
        ])
    story.extend([
        Paragraph(
            "Uncertainty is part of the forecast. The displayed band is the primary model output; the median is not a guaranteed target. "
            "Market data may be delayed or cached depending on the configured provider. Research and paper-trading simulation only; not investment advice.",
            styles["Italic"],
        ),
    ])
    document.build(story)
    return buffer.getvalue()
