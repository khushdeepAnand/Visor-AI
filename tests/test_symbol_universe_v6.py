from __future__ import annotations
from pathlib import Path
import csv
import pytest
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER

ROOT=Path(__file__).resolve().parents[1]


def test_bundled_symbol_universe_is_india_only():
    assert len(CATALOGUE.load()) > 100
    assert CATALOGUE.resolve("RELIANCE") is not None
    for bad in ["AAPL","MSFT","BTC-USD","ETH-USD","GC=F","CL=F"]:
        assert CATALOGUE.resolve(bad) is None
        with pytest.raises(ValueError): MANAGER.normalize_symbol(bad)


def test_market_csvs_do_not_contain_global_crypto_or_commodity_tickers():
    bad_tokens=("AAPL","MSFT","TSLA","NVDA","BTC-USD","ETH-USD","SOL-USD","=F")
    for path in (ROOT/"market_data").glob("*.csv"):
        text=path.read_text(encoding="utf-8",errors="ignore").upper()
        assert not any(token in text for token in bad_tokens), path.name
