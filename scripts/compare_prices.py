"""Compare live quotes from all configured market-data providers for a set of symbols.

This script queries each configured provider's get_quote for the given symbols and
reports differences. It also queries ProviderManager.get_quote to show the
provider chosen by the manager order.

Run from the repository root with: python scripts/compare_prices.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Add the repository root to sys.path so package imports resolve when run as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.market_data.manager import ProviderManager

SYMBOLS = ["NIFTY 50", "NIFTY BANK", "SENSEX"]

if __name__ == "__main__":
    manager = ProviderManager()
    available = {name: prov for name, prov in manager.providers.items() if prov.is_configured()}
    summary: dict[str, Any] = {
        "configured_providers": sorted(available),
        "symbols": {},
    }

    for sym in SYMBOLS:
        symbol_report: dict[str, Any] = {
            "manager_quote": None,
            "provider_quotes": {},
        }
        try:
            mq = manager.get_quote(sym)
            symbol_report["manager_quote"] = {"price": mq.price, "previous_close": mq.previous_close, "source": mq.source}
        except Exception as e:
            symbol_report["manager_quote"] = {"error": str(e)}

        for name, prov in available.items():
            try:
                q = prov.get_quote(sym)
                symbol_report["provider_quotes"][name] = {"price": q.price, "previous_close": q.previous_close, "source": q.source}
            except Exception as e:
                symbol_report["provider_quotes"][name] = {"error": str(e)}

        summary["symbols"][sym] = symbol_report

    print(json.dumps(summary, indent=2, ensure_ascii=False))
