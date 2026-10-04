"""Check Upstox provider configuration and try a quick quote request if credentials exist."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.market_data.upstox import UpstoxProvider

prov = UpstoxProvider()
print({
    "is_configured": prov.is_configured(),
    "credential_mode": prov.credential_mode,
})

if prov.is_configured():
    for sym in ("MARUTI", "NIFTY 50"):
        try:
            q = prov.get_quote(sym)
            print(sym, "ok", {"price": q.price, "previous_close": q.previous_close, "source": q.source})
        except Exception as e:
            print(sym, "error", str(e))
else:
    print("Upstox not configured — no token found in environment.")
