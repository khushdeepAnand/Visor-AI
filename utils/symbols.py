"""India-only symbol search compatibility helpers."""
from __future__ import annotations
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER

COMMON_SYMBOLS = {
    "reliance":"RELIANCE", "tcs":"TCS", "infosys":"INFY", "infy":"INFY",
    "sbi":"SBIN", "icici":"ICICIBANK", "hdfc":"HDFCBANK", "adani":"ADANIENT",
    "hal":"HAL", "bel":"BEL", "ireda":"IREDA", "nifty":"NIFTY 50",
    "banknifty":"NIFTY BANK", "sensex":"SENSEX",
}

def valid_symbol(symbol: str) -> bool:
    try:
        clean=MANAGER.normalize_symbol(symbol)
        return CATALOGUE.resolve(clean) is not None or bool(CATALOGUE.search(clean,1))
    except ValueError:
        return False

def get_symbol(search: str | None) -> str:
    if not search or not str(search).strip(): return ""
    raw=str(search).strip(); key=raw.lower()
    if key in COMMON_SYMBOLS: return COMMON_SYMBOLS[key]
    clean=MANAGER.normalize_symbol(raw)
    item=CATALOGUE.resolve(clean)
    return item.symbol if item else clean
