"""Live Upstox derivatives helpers with explicit offline fallbacks.

No result from this module is described as live unless Upstox actually returned it.
Instrument discovery can operate from the public/refreshed instrument master, while
option-chain and margin values require an authenticated Upstox token.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from derivatives.options_engine import black_scholes, implied_volatility
from services.market_data.instruments import CATALOGUE
from services.market_data.upstox import UpstoxProvider
from services.market_data.upstox_auth import assert_upstox_request_allowed
from services.market_data.context import build_market_context, utc_now
from services.paper_trading_v6 import estimate_margin


@dataclass(slots=True)
class MarginRequest:
    symbol: str
    quantity: int
    transaction_type: str = "BUY"
    product: str = "D"
    instrument_type: str = "FUTURE"
    price: float = 0.0
    lot_size: float = 1.0


class LiveDerivativesService:
    def __init__(self, provider: UpstoxProvider | None = None, timeout: float = 15.0) -> None:
        self.provider = provider or UpstoxProvider()
        self.timeout = timeout

    def contracts(self, underlying: str, expiry: str | None = None) -> list[dict[str, Any]]:
        clean = underlying.strip().upper().replace(".NS", "").replace(".BO", "")
        items = []
        for item in CATALOGUE.load():
            if item.segment not in {"NSE_FO", "BSE_FO"}:
                continue
            under = (item.underlying_symbol or "").upper()
            if under != clean:
                continue
            if expiry and str(item.expiry) != str(expiry):
                continue
            if item.option_type not in {"CE", "PE"}:
                continue
            items.append(item.to_dict())
        items.sort(key=lambda row: (str(row.get("expiry") or ""), float(row.get("strike") or 0), str(row.get("option_type") or "")))
        return items

    def expiries(self, underlying: str) -> list[str]:
        return sorted({str(row["expiry"]) for row in self.contracts(underlying) if row.get("expiry")})

    def _context(
        self,
        symbol: str,
        *,
        provider: str,
        timeframe: str,
        as_of: str | None,
        is_live: bool,
        is_stale: bool,
        fallback_reason: str | None = None,
    ) -> dict[str, Any]:
        return build_market_context(
            requested_symbol=symbol,
            instrument=CATALOGUE.resolve(symbol),
            provider=provider,
            credential_mode=str(getattr(self.provider, "credential_mode", "none")),
            timeframe=timeframe,
            as_of=as_of,
            is_live=is_live,
            is_stale=is_stale,
            fallback_used=fallback_reason is not None,
            fallback_reason=fallback_reason,
        )

    def live_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        if not self.provider.is_configured():
            reason = "Upstox token is not configured; live LTP/OI/IV/depth are unavailable."
            return {
                "underlying": underlying,
                "expiry": expiry,
                "source": "instrument_master_only",
                "is_live": False,
                "is_stale": True,
                "rows": self._master_only_rows(underlying, expiry),
                "message": reason,
                "context": self._context(underlying, provider="instrument_master", timeframe="option_chain", as_of=None, is_live=False, is_stale=True, fallback_reason=reason),
            }
        try:
            item = self.provider._instrument(underlying)
            response = requests.get(
                f"{self.provider.base_url}/v2/option/chain",
                params={"instrument_key": str(item.instrument_key), "expiry_date": expiry},
                headers=self.provider.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            rows = [self._normalize_chain_row(row) for row in payload.get("data", [])]
            fetched_at = utc_now()
            return {
                "underlying": item.symbol,
                "expiry": expiry,
                "source": "Upstox",
                "is_live": True,
                "is_stale": False,
                "fetched_at": fetched_at,
                "rows": rows,
                "context": self._context(item.symbol, provider="upstox", timeframe="option_chain", as_of=fetched_at, is_live=True, is_stale=False),
            }
        except Exception:
            reason = "Live option-chain data is unavailable; using the instrument master."
            return {
                "underlying": underlying,
                "expiry": expiry,
                "source": "instrument_master_fallback",
                "is_live": False,
                "is_stale": True,
                "rows": self._master_only_rows(underlying, expiry),
                "message": reason,
                "context": self._context(underlying, provider="instrument_master", timeframe="option_chain", as_of=None, is_live=False, is_stale=True, fallback_reason=reason),
            }

    @staticmethod
    def _normalize_side(side: dict[str, Any] | None, option_type: str) -> dict[str, Any] | None:
        if not side:
            return None
        market = side.get("market_data") or side.get("marketData") or {}
        greeks = side.get("option_greeks") or side.get("optionGreeks") or {}
        oi = market.get("oi")
        prev_oi = market.get("prev_oi") if market.get("prev_oi") is not None else market.get("prevOi")
        oi_change = None
        if oi is not None and prev_oi is not None:
            try:
                oi_change = float(oi) - float(prev_oi)
            except (TypeError, ValueError):
                oi_change = None

        bid_price = market.get("bid_price") if market.get("bid_price") is not None else market.get("bidPrice")
        bid_qty = market.get("bid_qty") if market.get("bid_qty") is not None else market.get("bidQty")
        ask_price = market.get("ask_price") if market.get("ask_price") is not None else market.get("askPrice")
        ask_qty = market.get("ask_qty") if market.get("ask_qty") is not None else market.get("askQty")
        depth = {
            "bids": [[bid_price, bid_qty]] if bid_price is not None and bid_qty is not None else [],
            "asks": [[ask_price, ask_qty]] if ask_price is not None and ask_qty is not None else [],
        }
        return {
            "instrument_key": side.get("instrument_key") or side.get("instrumentKey"),
            "option_type": option_type,
            "ltp": market.get("ltp"),
            "open_interest": oi,
            "previous_open_interest": prev_oi,
            "oi_change": oi_change,
            "volume": market.get("volume"),
            "iv": greeks.get("iv"),
            "delta": greeks.get("delta"),
            "gamma": greeks.get("gamma"),
            "theta": greeks.get("theta"),
            "vega": greeks.get("vega"),
            "depth": depth if depth["bids"] or depth["asks"] else None,
        }

    def _normalize_chain_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "expiry": row.get("expiry"),
            "strike": row.get("strike_price") if row.get("strike_price") is not None else row.get("strikePrice"),
            "underlying_spot": row.get("underlying_spot_price") if row.get("underlying_spot_price") is not None else row.get("underlyingSpotPrice"),
            "ce": self._normalize_side(row.get("call_options") or row.get("callOptions"), "CE"),
            "pe": self._normalize_side(row.get("put_options") or row.get("putOptions"), "PE"),
        }

    def _master_only_rows(self, underlying: str, expiry: str) -> list[dict[str, Any]]:
        paired: dict[float, dict[str, Any]] = {}
        for row in self.contracts(underlying, expiry):
            strike = float(row.get("strike") or 0)
            bucket = paired.setdefault(strike, {"expiry": expiry, "strike": strike, "underlying_spot": None, "ce": None, "pe": None})
            side = {
                "instrument_key": row.get("instrument_key"),
                "option_type": row.get("option_type"),
                "ltp": None,
                "open_interest": None,
                "oi_change": None,
                "volume": None,
                "iv": None,
                "delta": None,
                "gamma": None,
                "theta": None,
                "vega": None,
                "depth": None,
            }
            bucket["ce" if row.get("option_type") == "CE" else "pe"] = side
        return [paired[key] for key in sorted(paired)]

    def margin(self, request: MarginRequest) -> dict[str, Any]:
        instrument = CATALOGUE.resolve(request.symbol)
        if self.provider.is_configured() and instrument is not None and instrument.instrument_key:
            instrument_payload: dict[str, Any] = {
                "instrument_key": instrument.instrument_key,
                "quantity": int(request.quantity),
                "transaction_type": request.transaction_type.upper(),
                "product": request.product.upper(),
            }
            if request.price > 0:
                instrument_payload["price"] = float(request.price)
            body = {"instruments": [instrument_payload]}
            try:
                margin_url = f"{self.provider.base_url}/v2/charges/margin"
                assert_upstox_request_allowed("POST", margin_url)
                response = requests.post(
                    margin_url,
                    json=body,
                    headers={**self.provider.access_headers, "Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json().get("data", {})
                fetched_at = utc_now()
                return {
                    "source": "Upstox",
                    "approximate_margin": False,
                    "is_stale": False,
                    "fetched_at": fetched_at,
                    "required_margin": data.get("required_margin"),
                    "final_margin": data.get("final_margin"),
                    "margins": data.get("margins", []),
                    "context": self._context(request.symbol, provider="upstox", timeframe="margin", as_of=fetched_at, is_live=True, is_stale=False),
                }
            except Exception:
                error = "Broker margin service unavailable."
            else:  # pragma: no cover - kept for type clarity
                error = ""
        else:
            error = "Broker margin service unavailable."

        approx = estimate_margin(
            instrument_type=request.instrument_type.upper(),
            quantity=float(request.quantity),
            price=float(request.price),
            lot_size=float(request.lot_size),
            option_side=request.transaction_type.upper(),
        )
        fetched_at = utc_now()
        reason = f"Broker margin unavailable; using conservative approximation. {error}"
        return {
            "source": "StockPilot approximation",
            "approximate_margin": True,
            "is_stale": True,
            "fetched_at": fetched_at,
            "required_margin": approx,
            "final_margin": approx,
            "margins": [],
            "message": reason,
            "context": self._context(request.symbol, provider="stockpilot_approximation", timeframe="margin", as_of=fetched_at, is_live=False, is_stale=True, fallback_reason=reason),
        }


LIVE_DERIVATIVES = LiveDerivativesService()
