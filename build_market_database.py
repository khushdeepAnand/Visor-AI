"""Build the local symbol catalogue inside the primary StockPilot database."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from database import create_tables, get_connection

ROOT = Path(__file__).resolve().parent
FILES = [
    ROOT / "market_data" / "nse.csv",
    ROOT / "market_data" / "bse.csv",
    ROOT / "market_data" / "nasdaq.csv",
    ROOT / "market_data" / "nyse.csv",
    ROOT / "market_data" / "crypto.csv",
]
REQUIRED_COLUMNS = ["name", "symbol", "exchange", "country", "sector"]


def _read_catalogue(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        print(f"  skipped: {path.relative_to(ROOT)} (missing or empty)")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    try:
        frame = pd.read_csv(path)
    except (pd.errors.EmptyDataError, UnicodeDecodeError) as error:
        print(f"  skipped: {path.relative_to(ROOT)} ({error})")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        print(f"  skipped: {path.relative_to(ROOT)} (missing: {', '.join(missing)})")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    frame = frame[REQUIRED_COLUMNS].copy()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["name"] = frame["name"].fillna(frame["symbol"]).astype(str).str.strip()
    frame = frame[frame["symbol"].ne("") & frame["symbol"].ne("NAN")]
    return frame


def build() -> int:
    create_tables()
    frames = [_read_catalogue(path) for path in FILES]
    combined = pd.concat(frames, ignore_index=True)
    combined.drop_duplicates(subset=["symbol", "exchange"], keep="first", inplace=True)

    connection = get_connection()
    try:
        connection.execute("DELETE FROM symbols")
        connection.executemany(
            """
            INSERT INTO symbols(name, symbol, exchange, country, sector)
            VALUES (?, ?, ?, ?, ?)
            """,
            combined[REQUIRED_COLUMNS].itertuples(index=False, name=None),
        )
        connection.commit()
    finally:
        connection.close()

    print(f"Database ready. {len(combined)} symbols loaded into database/stockpilot.db.")
    return int(len(combined))


if __name__ == "__main__":
    build()
