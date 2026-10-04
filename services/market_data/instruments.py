"""India-only instrument catalogue.

The catalogue reads bundled NSE/BSE files and can also consume Upstox BOD JSON
instrument masters.  It intentionally filters out non-Indian segments.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

import requests

from .base import Instrument

ROOT = Path(__file__).resolve().parents[2]
MARKET_DIR = ROOT / "market_data"
UPSTOX_NSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
UPSTOX_BSE_URL = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
ALLOWED_SEGMENTS = {"NSE_EQ", "NSE_INDEX", "NSE_FO", "BSE_EQ", "BSE_INDEX", "BSE_FO"}
UPSTOX_INDEX_ALIASES = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK"}


def _clean_symbol(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if value.endswith(".NS") or value.endswith(".BO"):
        value = value[:-3]
    return value


def _normalize_expiry(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        # Upstox BOD JSON currently uses epoch milliseconds for derivative expiry.
        if number > 10_000_000_000:
            number /= 1000.0
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    text = str(value).strip()
    return text[:10] if len(text) >= 10 and text[4:5] == "-" else text


def _from_upstox_row(row: dict[str, Any]) -> Instrument | None:
    segment = str(row.get("segment") or "").upper()
    if segment not in ALLOWED_SEGMENTS:
        return None
    raw_symbol = row.get("trading_symbol") or row.get("tradingsymbol") or row.get("underlying_symbol")
    if not raw_symbol:
        return None
    symbol = _clean_symbol(str(raw_symbol))
    if segment.endswith("_INDEX"):
        symbol = UPSTOX_INDEX_ALIASES.get(symbol, symbol)
    itype = str(row.get("instrument_type") or "").upper()
    raw_option_type = str(row.get("option_type") or row.get("optionType") or "").upper()
    option_type = raw_option_type if raw_option_type in {"CE", "PE"} else (itype if itype in {"CE", "PE"} else None)
    return Instrument(
        symbol=symbol,
        name=str(row.get("name") or row.get("short_name") or symbol).strip(),
        exchange=str(row.get("exchange") or segment.split("_", 1)[0]).upper(),
        segment=segment,
        instrument_type=itype or ("INDEX" if segment.endswith("INDEX") else "EQ"),
        instrument_key=row.get("instrument_key"),
        isin=row.get("isin"),
        lot_size=float(row["lot_size"]) if row.get("lot_size") not in {None, ""} else None,
        expiry=_normalize_expiry(row.get("expiry")),
        strike=(
            float(str(row.get("strike_price") or row.get("strike")))
            if row.get("strike_price") or row.get("strike")
            else None
        ),
        option_type=option_type,
        underlying_symbol=(
            UPSTOX_INDEX_ALIASES.get(
                _clean_symbol(str(row.get("underlying_symbol"))),
                _clean_symbol(str(row.get("underlying_symbol"))),
            )
            if row.get("underlying_symbol")
            else None
        ),
        sector=str(row.get("sector") or row.get("industry") or "").strip() or None,
        market_cap_bucket=str(
            row.get("market_cap_bucket") or row.get("cap_bucket") or row.get("market_cap_category") or ""
        ).strip() or None,
        listing_year=_listing_year(row.get("listing_year") or row.get("ipo_year") or row.get("listing_date") or row.get("ipo_date")),
    )


def _listing_year(value: Any) -> int | None:
    """Parse optional listing metadata without guessing from unrelated dates."""
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        return year if 1900 <= year <= 2100 else None
    return None


class InstrumentCatalogue:
    def __init__(self) -> None:
        self._items: list[Instrument] | None = None
        self._lock = RLock()
        self._health: dict[str, Any] = {"source": "bundled_or_cache", "last_refresh_at": None, "is_stale": True}

    def _load_bundled(self) -> list[Instrument]:
        items: list[Instrument] = []
        for filename, exchange in (("nse.csv", "NSE"), ("bse.csv", "BSE")):
            path = MARKET_DIR / filename
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    symbol = _clean_symbol(row.get("symbol", ""))
                    if not symbol:
                        continue
                    items.append(Instrument(
                        symbol=symbol,
                        name=str(row.get("name") or symbol),
                        exchange=exchange,
                        segment=f"{exchange}_EQ",
                        instrument_type="EQ",
                        instrument_key=row.get("instrument_key") or None,
                        isin=row.get("isin") or None,
                        sector=str(row.get("sector") or row.get("industry") or "").strip() or None,
                        market_cap_bucket=str(row.get("market_cap_bucket") or row.get("cap_bucket") or "").strip() or None,
                        listing_year=_listing_year(row.get("listing_year") or row.get("ipo_year") or row.get("listing_date")),
                    ))
        # Core indices are always searchable even when only the exchange CSV is bundled.
        for symbol, name, exchange in [
            ("NIFTY 50", "NIFTY 50", "NSE"),
            ("NIFTY BANK", "NIFTY BANK", "NSE"),
            ("NIFTY IT", "NIFTY IT", "NSE"),
            ("NIFTY FIN SERVICE", "NIFTY FINANCIAL SERVICES", "NSE"),
            ("SENSEX", "S&P BSE SENSEX", "BSE"),
        ]:
            items.append(Instrument(symbol=symbol, name=name, exchange=exchange, segment=f"{exchange}_INDEX", instrument_type="INDEX"))
        return items

    @staticmethod
    def _merge_bundled_indices(items: list[Instrument], bundled: list[Instrument]) -> list[Instrument]:
        """Keep one core index per exchange, preferring a master row with a valid key."""
        merged = list(items)
        for fallback in (item for item in bundled if item.segment.endswith("_INDEX")):
            matches = [
                (index, item)
                for index, item in enumerate(merged)
                if item.symbol == fallback.symbol and item.exchange == fallback.exchange and item.segment.endswith("_INDEX")
            ]
            if not matches:
                merged.append(fallback)
                continue
            keyed = next(((index, item) for index, item in matches if item.instrument_key), None)
            keep_index, keep_item = keyed or matches[0]
            for index, _item in reversed(matches):
                if index != keep_index:
                    merged.pop(index)
            if not keep_item.instrument_key and fallback.instrument_key:
                merged[keep_index] = fallback
        return merged

    def load(self, force: bool = False) -> list[Instrument]:
        with self._lock:
            if self._items is not None and not force:
                return self._items
        cache = MARKET_DIR / "instruments_india.json"
        if cache.exists():
            try:
                raw = json.loads(cache.read_text(encoding="utf-8"))
                parsed = [_from_upstox_row(row) for row in raw if isinstance(row, dict)]
                items = [item for item in parsed if item is not None]
                if items:
                    # Ensure core indices (bundled) are always present. The Upstox
                    # cached masters sometimes omit these, which breaks code that
                    # expects indices like 'NIFTY 50' to be resolvable.
                    self._items = self._merge_bundled_indices(items, self._load_bundled())
                    return self._items
            except (OSError, ValueError, TypeError):
                pass
        self._items = self._load_bundled()
        return self._items

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {**self._health, "rows": len(self.load())}

    def search(self, query: str, limit: int = 20) -> list[Instrument]:
        q = _clean_symbol(query)
        if not q:
            return self.load()[:limit]
        ranked: list[tuple[int, Instrument]] = []
        for item in self.load():
            symbol = item.symbol.upper()
            name = item.name.upper()
            if q == symbol:
                score = 0
            elif symbol.startswith(q):
                score = 1
            elif q in symbol:
                score = 2
            elif name.startswith(q):
                score = 3
            elif q in name:
                score = 4
            else:
                continue
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1].symbol))
        return [item for _, item in ranked[: max(1, min(limit, 100))]]

    def resolve(self, symbol: str, exchange: str | None = None) -> Instrument | None:
        clean = _clean_symbol(symbol)
        exchange = exchange.upper() if exchange else None
        matches = self.search(clean, limit=100)
        for item in matches:
            if item.symbol == clean and (exchange is None or item.exchange == exchange):
                return item
        return None

    def refresh_from_upstox(self, timeout: float = 30.0) -> dict[str, Any]:
        """Refresh from Upstox's public BOD contract masters."""
        headers = {"User-Agent": "StockPilotAI/6.0", "Accept": "application/json,application/gzip"}
        merged: list[dict[str, Any]] = []
        errors: list[str] = []
        for url in (UPSTOX_NSE_URL, UPSTOX_BSE_URL):
            try:
                response = requests.get(url, headers=headers, timeout=timeout)
                response.raise_for_status()
                payload = gzip.decompress(response.content)
                rows = json.loads(payload.decode("utf-8"))
                merged.extend(row for row in rows if str(row.get("segment") or "").upper() in ALLOWED_SEGMENTS)
            except Exception as exc:  # network/provider boundary
                errors.append(f"{url}: {exc}")
        source = "upstox_bod"
        if not merged:
            raise RuntimeError("Unable to refresh instrument masters from public sources.")
        path = MARKET_DIR / "instruments_india.json"
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)
        with self._lock:
            self._items = None
            loaded = self.load(force=True)
            self._health = {
                "source": source,
                "last_refresh_at": datetime.now(timezone.utc).isoformat(),
                "is_stale": False,
                "failed_sources": len(errors),
            }
        return {"rows": len(loaded), "failed_sources": len(errors), "source": source}


CATALOGUE = InstrumentCatalogue()
