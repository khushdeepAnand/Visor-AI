"""Market, sector, VIX, derivatives, and flow features for StockPilot AI v13.

These features are admitted only if they improve out-of-sample interval score.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class MarketFeatures:
    """Market and sector context features for a single forecast origin."""
    nifty_return_1d: float | None
    nifty_return_5d: float | None
    nifty_return_20d: float | None
    banknifty_return_1d: float | None
    sector_return_1d: float | None
    sector_return_5d: float | None
    beta_nifty_20d: float | None
    beta_nifty_60d: float | None
    rolling_corr_nifty_20d: float | None
    sector_momentum_20d: float | None
    relative_strength_nifty_20d: float | None
    vix_level: float | None
    vix_change_1d: float | None
    vix_change_5d: float | None
    vix_percentile_252d: float | None
    expected_move_atm: float | None
    oi_change_pct: float | None
    pcr: float | None
    rollover_pct: float | None
    basis_pct: float | None
    max_pain_distance_pct: float | None
    fii_net_buy_cr: float | None
    dii_net_buy_cr: float | None
    delivery_pct: float | None
    bulk_deals_count: float | None
    block_deals_count: float | None
    promoter_pledge_change_pct: float | None
    # Fundamentals are deliberately point-in-time fields.  They are kept
    # separate from price/flow features so a stale filing can never be
    # mistaken for a value observed at the forecast origin.
    fundamental_pe: float | None = None
    fundamental_pb: float | None = None
    fundamental_roe: float | None = None
    fundamental_debt_to_equity: float | None = None
    fundamental_eps: float | None = None
    fundamental_as_of: str | None = None


class FeatureAdapter(Protocol):
    """Offline provider contract for flow/event/fundamental snapshots.

    Adapters return point-in-time dictionaries; they may be backed by a local
    cache or test fixture. No network behavior is implied by this interface.
    """
    def snapshot(self, *, symbol: str | None = None, as_of: Any = None) -> dict[str, Any]: ...


def adapter_snapshot(adapter: FeatureAdapter | None, *, symbol: str | None = None,
                     as_of: Any = None) -> dict[str, Any]:
    """Safely invoke an optional adapter, returning unavailable on failure."""
    if adapter is None:
        return {}
    try:
        value = adapter.snapshot(symbol=symbol, as_of=as_of)
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _safe_last(series: pd.Series) -> float | None:
    """Get last valid value from series."""
    if series is None or len(series) == 0:
        return None
    valid = series.dropna()
    if len(valid) == 0:
        return None
    return float(valid.iloc[-1])


def _rolling_beta(stock_returns: pd.Series, market_returns: pd.Series, window: int) -> float | None:
    """Compute rolling beta of stock vs market."""
    if len(stock_returns) < window or len(market_returns) < window:
        return None
    stock = stock_returns.iloc[-window:]
    market = market_returns.iloc[-window:]
    if market.var() == 0:
        return None
    cov = stock.cov(market)
    return float(cov / market.var()) if np.isfinite(cov) else None


def _rolling_corr(series1: pd.Series, series2: pd.Series, window: int) -> float | None:
    """Compute rolling correlation."""
    if len(series1) < window or len(series2) < window:
        return None
    s1 = series1.iloc[-window:]
    s2 = series2.iloc[-window:]
    corr = s1.corr(s2)
    return float(corr) if np.isfinite(corr) else None


def _as_of_value(value: Any) -> pd.Timestamp | None:
    """Parse a provider timestamp without assuming local time."""
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC")
    except (TypeError, ValueError):
        return None


def _point_in_time_record(data: Any, as_of: pd.Timestamp | None) -> dict[str, Any]:
    """Select the last provider record available at ``as_of``.

    Providers use either ``history``/``observations`` or a single dictionary.
    A future observation is never silently admitted; this is the leakage guard
    for FII/DII, delivery and fundamentals.
    """
    if not isinstance(data, dict):
        return {}
    records = data.get("history", data.get("observations"))
    if not isinstance(records, (list, tuple)):
        record = dict(data)
        record.pop("history", None); record.pop("observations", None)
        stamp = _as_of_value(record.get("as_of", record.get("observed_at", record.get("date"))))
        if as_of is not None and stamp is not None and stamp > as_of:
            return {}
        return record
    chosen: dict[str, Any] | None = None
    chosen_stamp: pd.Timestamp | None = None
    for item in records:
        if not isinstance(item, dict):
            continue
        stamp = _as_of_value(item.get("as_of", item.get("observed_at", item.get("date"))))
        if as_of is not None and stamp is not None and stamp > as_of:
            continue
        if chosen is None or (stamp is not None and (chosen_stamp is None or stamp >= chosen_stamp)):
            chosen, chosen_stamp = dict(item), stamp
    return chosen or {}


def compute_market_features(
    symbol_frame: pd.DataFrame,
    nifty_frame: pd.DataFrame | None = None,
    banknifty_frame: pd.DataFrame | None = None,
    sector_frame: pd.DataFrame | None = None,
    vix_series: pd.Series | None = None,
    option_chain: dict[str, Any] | None = None,
    futures_data: dict[str, Any] | None = None,
    flow_data: dict[str, Any] | None = None,
    fundamental_data: dict[str, Any] | None = None,
    as_of: Any = None,
) -> MarketFeatures:
    """Compute all market/sector/VIX/derivatives/flow features for the latest bar.

    All features are point-in-time: they only use data available at the latest
    timestamp of symbol_frame.
    """
    # Providers differ on OHLCV casing; normalize a copy without mutating the
    # caller.  Missing close is unavailable data, never a zero-valued feature.
    def _close(frame: pd.DataFrame | None) -> pd.Series:
        if frame is None:
            return pd.Series(dtype=float)
        columns = {str(c).strip().lower(): c for c in frame.columns}
        name = columns.get("close")
        return pd.to_numeric(frame[name], errors="coerce") if name is not None else pd.Series(dtype=float)

    # Forecast origin is the last valid symbol observation unless explicitly
    # provided.  Flow/fundamental histories are filtered against this instant.
    origin = _as_of_value(as_of)
    if origin is None and len(symbol_frame.index):
        origin = _as_of_value(symbol_frame.index[-1])

    # Symbol returns
    symbol_close = _close(symbol_frame)
    symbol_returns = symbol_close.pct_change().dropna()

    # Market features
    nifty_return_1d = nifty_return_5d = nifty_return_20d = None
    banknifty_return_1d = None
    beta_nifty_20d = beta_nifty_60d = None
    rolling_corr_nifty_20d = None

    if nifty_frame is not None and len(nifty_frame) > 1:
        nifty_close = _close(nifty_frame)
        nifty_returns = nifty_close.pct_change().dropna()
        nifty_return_1d = _safe_last(nifty_returns)
        nifty_return_5d = _safe_last(nifty_close.pct_change(5))
        nifty_return_20d = _safe_last(nifty_close.pct_change(20))

        # Beta and correlation
        common_idx = symbol_returns.index.intersection(nifty_returns.index)
        if len(common_idx) >= 20:
            s_ret = symbol_returns.loc[common_idx]
            n_ret = nifty_returns.loc[common_idx]
            beta_nifty_20d = _rolling_beta(s_ret, n_ret, 20)
            beta_nifty_60d = _rolling_beta(s_ret, n_ret, 60) if len(common_idx) >= 60 else None
            rolling_corr_nifty_20d = _rolling_corr(s_ret, n_ret, 20)

    if banknifty_frame is not None and len(banknifty_frame) > 1:
        bn_close = _close(banknifty_frame)
        banknifty_return_1d = _safe_last(bn_close.pct_change())

    # Sector features
    sector_return_1d = sector_return_5d = sector_momentum_20d = None
    relative_strength_nifty_20d = None
    if sector_frame is not None and len(sector_frame) > 1:
        sector_close = _close(sector_frame)
        sector_returns = sector_close.pct_change().dropna()
        sector_return_1d = _safe_last(sector_returns)
        sector_return_5d = _safe_last(sector_close.pct_change(5))
        sector_momentum_20d = _safe_last(sector_close.pct_change(20))
        if nifty_return_20d is not None and sector_momentum_20d is not None:
            relative_strength_nifty_20d = sector_momentum_20d - nifty_return_20d

    # VIX features
    vix_level = vix_change_1d = vix_change_5d = vix_percentile_252d = None
    if vix_series is not None and len(vix_series) > 1:
        vix_clean = pd.to_numeric(vix_series, errors="coerce").dropna()
        vix_level = _safe_last(vix_clean)
        vix_change_1d = _safe_last(vix_clean.pct_change())
        vix_change_5d = _safe_last(vix_clean.pct_change(5))
        if len(vix_clean) >= 252:
            vix_percentile_252d = float((vix_clean < vix_clean.iloc[-1]).mean())

    # Options-implied expected move
    expected_move_atm = None
    if option_chain and option_chain.get("available"):
        expected_move_atm = option_chain.get("expected_moves", [{}])[0].get("move_pct")

    # Derivatives positioning
    oi_change_pct = pcr = rollover_pct = basis_pct = max_pain_distance_pct = None
    if option_chain and option_chain.get("available"):
        oi_change_pct = option_chain.get("oi_change_pct")
        if oi_change_pct is None:
            now_oi = option_chain.get("total_open_interest", option_chain.get("open_interest"))
            prev_oi = option_chain.get("previous_open_interest")
            try:
                if prev_oi is not None and now_oi is not None and float(prev_oi) > 0:
                    oi_change_pct = (float(now_oi) - float(prev_oi)) / float(prev_oi) * 100.0
            except (TypeError, ValueError):
                pass
        pcr = option_chain.get("pcr", option_chain.get("put_call_ratio"))
    if futures_data:
        rollover_pct = futures_data.get("rollover_pct")
        basis_pct = futures_data.get("basis_pct")
        max_pain_distance_pct = futures_data.get("max_pain_distance_pct")

    # Flow data
    fii_net_buy_cr = dii_net_buy_cr = delivery_pct = None
    bulk_deals_count = block_deals_count = promoter_pledge_change_pct = None
    flow = _point_in_time_record(flow_data, origin)
    if flow:
        fii_net_buy_cr = flow.get("fii_net_buy_cr")
        dii_net_buy_cr = flow.get("dii_net_buy_cr")
        delivery_pct = flow.get("delivery_pct")
        bulk_deals_count = flow.get("bulk_deals_count")
        block_deals_count = flow.get("block_deals_count")
        promoter_pledge_change_pct = flow.get("promoter_pledge_change_pct")

    fundamentals = _point_in_time_record(fundamental_data, origin)
    fundamental_pe = fundamentals.get("pe", fundamentals.get("fundamental_pe"))
    fundamental_pb = fundamentals.get("pb", fundamentals.get("fundamental_pb"))
    fundamental_roe = fundamentals.get("roe", fundamentals.get("fundamental_roe"))
    fundamental_debt_to_equity = fundamentals.get("debt_to_equity", fundamentals.get("fundamental_debt_to_equity"))
    fundamental_eps = fundamentals.get("eps", fundamentals.get("fundamental_eps"))
    fundamental_as_of = fundamentals.get("as_of", fundamentals.get("observed_at", fundamentals.get("date")))

    return MarketFeatures(
        nifty_return_1d=nifty_return_1d,
        nifty_return_5d=nifty_return_5d,
        nifty_return_20d=nifty_return_20d,
        banknifty_return_1d=banknifty_return_1d,
        sector_return_1d=sector_return_1d,
        sector_return_5d=sector_return_5d,
        beta_nifty_20d=beta_nifty_20d,
        beta_nifty_60d=beta_nifty_60d,
        rolling_corr_nifty_20d=rolling_corr_nifty_20d,
        sector_momentum_20d=sector_momentum_20d,
        relative_strength_nifty_20d=relative_strength_nifty_20d,
        vix_level=vix_level,
        vix_change_1d=vix_change_1d,
        vix_change_5d=vix_change_5d,
        vix_percentile_252d=vix_percentile_252d,
        expected_move_atm=expected_move_atm,
        oi_change_pct=oi_change_pct,
        pcr=pcr,
        rollover_pct=rollover_pct,
        basis_pct=basis_pct,
        max_pain_distance_pct=max_pain_distance_pct,
        fii_net_buy_cr=fii_net_buy_cr,
        dii_net_buy_cr=dii_net_buy_cr,
        delivery_pct=delivery_pct,
        bulk_deals_count=bulk_deals_count,
        block_deals_count=block_deals_count,
        promoter_pledge_change_pct=promoter_pledge_change_pct,
        fundamental_pe=fundamental_pe,
        fundamental_pb=fundamental_pb,
        fundamental_roe=fundamental_roe,
        fundamental_debt_to_equity=fundamental_debt_to_equity,
        fundamental_eps=fundamental_eps,
        fundamental_as_of=None if fundamental_as_of is None else str(fundamental_as_of),
    )


def market_features_to_dict(features: MarketFeatures) -> dict[str, Any]:
    """Convert MarketFeatures to dictionary for API response."""
    return {
        "market_context": {
            "nifty_return_1d": features.nifty_return_1d,
            "nifty_return_5d": features.nifty_return_5d,
            "nifty_return_20d": features.nifty_return_20d,
            "banknifty_return_1d": features.banknifty_return_1d,
        },
        "sector_context": {
            "sector_return_1d": features.sector_return_1d,
            "sector_return_5d": features.sector_return_5d,
            "sector_momentum_20d": features.sector_momentum_20d,
            "relative_strength_nifty_20d": features.relative_strength_nifty_20d,
        },
        "systematic_risk": {
            "beta_nifty_20d": features.beta_nifty_20d,
            "beta_nifty_60d": features.beta_nifty_60d,
            "rolling_corr_nifty_20d": features.rolling_corr_nifty_20d,
        },
        "vix": {
            "level": features.vix_level,
            "change_1d": features.vix_change_1d,
            "change_5d": features.vix_change_5d,
            "percentile_252d": features.vix_percentile_252d,
        },
        "options_implied": {
            "expected_move_atm_pct": features.expected_move_atm,
        },
        "derivatives_positioning": {
            "oi_change_pct": features.oi_change_pct,
            "pcr": features.pcr,
            "rollover_pct": features.rollover_pct,
            "basis_pct": features.basis_pct,
            "max_pain_distance_pct": features.max_pain_distance_pct,
        },
        "flows": {
            "fii_net_buy_cr": features.fii_net_buy_cr,
            "dii_net_buy_cr": features.dii_net_buy_cr,
            "delivery_pct": features.delivery_pct,
            "bulk_deals_count": features.bulk_deals_count,
            "block_deals_count": features.block_deals_count,
            "promoter_pledge_change_pct": features.promoter_pledge_change_pct,
        },
        "fundamentals": {
            "pe": features.fundamental_pe,
            "pb": features.fundamental_pb,
            "roe": features.fundamental_roe,
            "debt_to_equity": features.fundamental_debt_to_equity,
            "eps": features.fundamental_eps,
            "as_of": features.fundamental_as_of,
            "basis": "latest disclosed record available at forecast origin",
        },
        "disclosure": (
            "Market/sector/VIX/derivatives/flow features are experimental and only included "
            "if they improve out-of-sample interval score in walk-forward tests."
        ),
    }


def evaluate_feature_importance(
    feature_names: Sequence[str],
    feature_values: Sequence[float],
    interval_score_improvement: float,
    min_improvement: float = 0.001,
) -> dict[str, Any]:
    """Evaluate if a feature set improves interval score enough to be admitted.

    Args:
        feature_names: Names of features tested
        feature_values: Their values at forecast origin
        interval_score_improvement: Improvement in Winkler/interval score vs baseline
        min_improvement: Minimum improvement threshold

    Returns:
        Dict with admission decision and reasoning
    """
    admitted = interval_score_improvement >= min_improvement
    return {
        "features_tested": list(feature_names),
        "feature_values": list(feature_values),
        "interval_score_improvement": round(interval_score_improvement, 6),
        "min_improvement_threshold": min_improvement,
        "admitted": admitted,
        "reason": (
            f"Feature set improved interval score by {interval_score_improvement:.6f} "
            f"({'above' if admitted else 'below'} threshold of {min_improvement:.6f})"
        ),
    }


__all__: Sequence[str] = (
    "MarketFeatures",
    "FeatureAdapter",
    "adapter_snapshot",
    "compute_market_features",
    "market_features_to_dict",
    "evaluate_feature_importance",
)
