"""Corporate-action adjustment for StockPilot AI v13.

Handles splits, bonuses, rights, dividends, mergers/demergers, symbol changes, ISIN changes.
A single unadjusted split ruins a model. This module provides automated adjustment with
tests using known historical splits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Sequence

import numpy as np
import pandas as pd


class CorporateActionType(Enum):
    SPLIT = "split"
    BONUS = "bonus"
    RIGHTS = "rights"
    DIVIDEND = "dividend"
    MERGER = "merger"
    DEMERGER = "demerger"
    SYMBOL_CHANGE = "symbol_change"
    ISIN_CHANGE = "isin_change"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """A single corporate action event."""
    symbol: str
    action_type: CorporateActionType
    ex_date: date
    ratio_numerator: float | None = None   # e.g., 2 for 2:1 split
    ratio_denominator: float | None = None  # e.g., 1 for 2:1 split
    bonus_ratio: float | None = None       # e.g., 1 for 1:1 bonus
    rights_ratio: float | None = None      # e.g., 1 for 1:5 rights
    rights_price: float | None = None
    dividend_per_share: float | None = None
    new_symbol: str | None = None
    new_isin: str | None = None
    details: dict[str, Any] | None = None
    source_isin: str | None = None
    target_isins: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    """Result of applying corporate action adjustments."""
    adjusted_frame: pd.DataFrame
    actions_applied: list[CorporateAction]
    warnings: list[str]


# Known historical corporate actions for testing (NSE/BSE major events)
KNOWN_ACTIONS: dict[str, list[CorporateAction]] = {
    "RELIANCE": [
        CorporateAction("RELIANCE", CorporateActionType.SPLIT, date(1997, 10, 8), ratio_numerator=2, ratio_denominator=1),
        CorporateAction("RELIANCE", CorporateActionType.SPLIT, date(2009, 11, 12), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
    ],
    "TCS": [
        CorporateAction("TCS", CorporateActionType.SPLIT, date(2018, 6, 15), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
    ],
    "INFY": [
        CorporateAction("INFY", CorporateActionType.SPLIT, date(1999, 7, 12), ratio_numerator=2, ratio_denominator=1),
        CorporateAction("INFY", CorporateActionType.SPLIT, date(2006, 7, 10), ratio_numerator=2, ratio_denominator=1),
        CorporateAction("INFY", CorporateActionType.SPLIT, date(2014, 12, 12), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
    ],
    "HDFCBANK": [
        CorporateAction("HDFCBANK", CorporateActionType.SPLIT, date(2011, 9, 15), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
        CorporateAction("HDFCBANK", CorporateActionType.SPLIT, date(2019, 9, 18), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
    ],
    "ITC": [
        CorporateAction("ITC", CorporateActionType.SPLIT, date(2005, 11, 11), ratio_numerator=10, ratio_denominator=1),  # 10:1 split
    ],
    "SBIN": [
        CorporateAction("SBIN", CorporateActionType.SPLIT, date(2014, 11, 21), ratio_numerator=10, ratio_denominator=1),  # 10:1 split
    ],
    "HINDUNILVR": [
        CorporateAction("HINDUNILVR", CorporateActionType.SPLIT, date(2017, 11, 15), ratio_numerator=1, ratio_denominator=1),  # 1:1 bonus
    ],
}


def _adjust_for_split(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Adjust OHLCV for a stock split.

    Prices BEFORE ex-date are divided by split ratio (adjusted down).
    Prices ON/AFTER ex-date are already split-adjusted (market prices).
    Volume BEFORE ex-date is multiplied by split ratio.
    """
    if action.ratio_numerator is None or action.ratio_denominator is None:
        return frame
    factor = action.ratio_numerator / action.ratio_denominator  # e.g., 2 for 2:1 split
    adjusted = frame.copy()
    ex_ts = pd.Timestamp(action.ex_date)
    pre_mask = adjusted.index < ex_ts
    for col in ["Open", "High", "Low", "Close"]:
        if col in adjusted.columns:
            adjusted.loc[pre_mask, col] = adjusted.loc[pre_mask, col] / factor
    if "Volume" in adjusted.columns:
        adjusted.loc[pre_mask, "Volume"] = adjusted.loc[pre_mask, "Volume"] * factor
    return adjusted


def _adjust_for_bonus(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Adjust for bonus issue (similar to split).

    Bonus ratio of 1:1 means 1 bonus share for each held = 2x shares total.
    Prices BEFORE ex-date are divided by (1 + bonus_ratio).
    """
    if action.bonus_ratio is None:
        return frame
    factor = 1.0 + action.bonus_ratio
    adjusted = frame.copy()
    ex_ts = pd.Timestamp(action.ex_date)
    pre_mask = adjusted.index < ex_ts
    for col in ["Open", "High", "Low", "Close"]:
        if col in adjusted.columns:
            adjusted.loc[pre_mask, col] = adjusted.loc[pre_mask, col] / factor
    if "Volume" in adjusted.columns:
        adjusted.loc[pre_mask, "Volume"] = adjusted.loc[pre_mask, "Volume"] * factor
    return adjusted


def _adjust_for_rights(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Adjust for rights issue using TERP (Theoretical Ex-Rights Price)."""
    if action.rights_ratio is None or action.rights_price is None:
        return frame
    adjusted = frame.copy()
    ex_ts = pd.Timestamp(action.ex_date)
    pre_mask = adjusted.index < ex_ts
    # TERP adjustment factor
    factor = 1.0 / (1.0 + action.rights_ratio)
    for col in ["Open", "High", "Low", "Close"]:
        if col in adjusted.columns:
            adjusted.loc[pre_mask, col] = adjusted.loc[pre_mask, col] * factor
    if "Volume" in adjusted.columns:
        adjusted.loc[pre_mask, "Volume"] = adjusted.loc[pre_mask, "Volume"] / factor
    return adjusted


def _adjust_for_dividend(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Adjust for dividend (reduce price by dividend amount on ex-date).

    Prices ON/AFTER ex-date are already ex-dividend.
    Prices BEFORE ex-date need to be reduced by dividend amount for continuity.
    """
    if action.dividend_per_share is None:
        return frame
    adjusted = frame.copy()
    ex_ts = pd.Timestamp(action.ex_date)
    pre_mask = adjusted.index < ex_ts
    div = action.dividend_per_share
    for col in ["Open", "High", "Low", "Close"]:
        if col in adjusted.columns:
            adjusted.loc[pre_mask, col] = adjusted.loc[pre_mask, col] - div
    return adjusted


def _adjust_for_symbol_change(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Symbol change doesn't require price adjustment, just metadata."""
    return frame


def _adjust_for_entity_event(frame: pd.DataFrame, action: CorporateAction) -> pd.DataFrame:
    """Apply explicit historical price factor for merger/demerger schemes.

    Providers do not expose a universal adjustment rule for schemes of
    arrangement. A caller may supply ``details.price_factor``: the multiplier
    applied to pre-ex-date historical prices. Without that term, withholding
    the numeric adjustment is safer than inventing an exchange ratio.
    """
    details = action.details or {}
    try:
        raw_factor = details.get("price_factor")
        if raw_factor is None:
            return frame
        factor = float(raw_factor)
    except (TypeError, ValueError):
        return frame
    if not np.isfinite(factor) or factor <= 0:
        return frame
    adjusted = frame.copy()
    pre_mask = adjusted.index < pd.Timestamp(action.ex_date)
    for col in ["Open", "High", "Low", "Close"]:
        if col in adjusted.columns:
            adjusted.loc[pre_mask, col] = adjusted.loc[pre_mask, col] * factor
    if "Volume" in adjusted.columns:
        adjusted.loc[pre_mask, "Volume"] = adjusted.loc[pre_mask, "Volume"] / factor
    return adjusted


def apply_corporate_actions(
    frame: pd.DataFrame,
    symbol: str,
    *,
    actions: list[CorporateAction] | None = None,
    auto_fetch: bool = False,
) -> AdjustmentResult:
    """Apply all corporate actions to a price frame.

    Args:
        frame: DataFrame with OHLCV data (index must be DatetimeIndex)
        symbol: Symbol to adjust
        actions: Explicit list of actions (if None, uses KNOWN_ACTIONS)
        auto_fetch: If True, would fetch from provider (not implemented)

    Returns:
        AdjustmentResult with adjusted frame and metadata
    """
    if frame is None or frame.empty:
        raise ValueError("Frame cannot be empty")

    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index)

    requested_symbol = str(symbol or "").strip().upper()
    actions_to_apply = actions if actions is not None else KNOWN_ACTIONS.get(requested_symbol, [])
    actions_to_apply = sorted(actions_to_apply, key=lambda a: a.ex_date)

    adjusted = frame.copy()
    applied: list[CorporateAction] = []
    warnings: list[str] = []

    # Never silently apply an event belonging to another issuer.  This matters
    # for merged/demerged entities where a provider can return a mixed action
    # feed.  Callers may attach an ISIN to the frame; an explicit action ISIN
    # must match it (or the event is withheld rather than fabricated).
    frame_isin = str(frame.attrs.get("isin") or "").strip().upper()

    for action in actions_to_apply:
        try:
            action_symbol = str(action.symbol or "").strip().upper()
            if action_symbol and action_symbol != requested_symbol:
                warnings.append(f"Skipped {action.action_type.value} for {action_symbol}: symbol mismatch for {requested_symbol}")
                continue
            source_isin = str(action.source_isin or (action.details or {}).get("source_isin") or "").strip().upper()
            if frame_isin and source_isin and source_isin != frame_isin:
                warnings.append(f"Skipped {action.action_type.value} on {action.ex_date}: source ISIN mismatch")
                continue
            if action.action_type == CorporateActionType.SPLIT:
                adjusted = _adjust_for_split(adjusted, action)
            elif action.action_type == CorporateActionType.BONUS:
                adjusted = _adjust_for_bonus(adjusted, action)
            elif action.action_type == CorporateActionType.RIGHTS:
                adjusted = _adjust_for_rights(adjusted, action)
            elif action.action_type == CorporateActionType.DIVIDEND:
                adjusted = _adjust_for_dividend(adjusted, action)
            elif action.action_type == CorporateActionType.SYMBOL_CHANGE:
                adjusted = _adjust_for_symbol_change(adjusted, action)
            elif action.action_type in (CorporateActionType.MERGER, CorporateActionType.DEMERGER):
                details = action.details or {}
                if details.get("price_factor") is None:
                    warnings.append(
                        f"Withheld {action.action_type.value} on {action.ex_date}: explicit price_factor required for entity-level adjustment"
                    )
                    continue
                adjusted = _adjust_for_entity_event(adjusted, action)
            elif action.action_type == CorporateActionType.ISIN_CHANGE:
                if not action.new_isin:
                    warnings.append(f"Withheld ISIN change on {action.ex_date}: target ISIN missing")
                    continue
            applied.append(action)
        except Exception as e:
            warnings.append(f"Failed to apply {action.action_type.value} for {symbol}: {e}")

    return AdjustmentResult(
        adjusted_frame=adjusted,
        actions_applied=applied,
        warnings=warnings,
    )


def verify_adjustment_correctness(
    frame: pd.DataFrame,
    symbol: str,
    *,
    known_actions: list[CorporateAction] | None = None,
) -> dict[str, Any]:
    """Verify that corporate actions are correctly adjusted.

    Checks:
    1. No price jumps at ex-dates beyond normal volatility
    2. Volume adjusts correctly for splits/bonuses
    3. Returns are continuous after adjustment
    """
    actions = known_actions if known_actions is not None else KNOWN_ACTIONS.get(symbol.upper(), [])
    if not actions:
        return {"verified": True, "checks": [], "message": "No known actions to verify"}

    result = apply_corporate_actions(frame, symbol, actions=actions)
    adjusted = result.adjusted_frame

    checks: list[dict[str, Any]] = []
    for action in actions:
        ex_date = pd.Timestamp(action.ex_date)
        # Find closest trading day to ex-date
        if len(adjusted) == 0:
            continue
        idx = adjusted.index.get_indexer(pd.DatetimeIndex([ex_date]), method="nearest")[0]
        if idx < 1 or idx >= len(adjusted) - 1:
            continue

        pre_close = adjusted["Close"].iloc[idx - 1]
        post_close = adjusted["Close"].iloc[idx]
        if pre_close > 0:
            jump = abs(post_close - pre_close) / pre_close
            checks.append({
                "action": action.action_type.value,
                "ex_date": str(action.ex_date),
                "price_jump_pct": round(jump * 100, 4),
                "acceptable": jump < 0.15,  # 15% threshold for normal volatility
            })

        # Volume check for splits/bonuses
        if action.action_type in (CorporateActionType.SPLIT, CorporateActionType.BONUS):
            pre_vol = adjusted["Volume"].iloc[idx - 1] if "Volume" in adjusted.columns else 0
            post_vol = adjusted["Volume"].iloc[idx] if "Volume" in adjusted.columns else 0
            if pre_vol > 0 and post_vol > 0:
                # After adjustment, pre-split volume is multiplied by split factor
                # So pre_vol / post_vol should equal the split factor
                vol_ratio = pre_vol / post_vol
                expected_ratio = (action.ratio_numerator / action.ratio_denominator) if action.ratio_numerator and action.ratio_denominator else (1 + (action.bonus_ratio or 0))
                checks.append({
                    "action": action.action_type.value,
                    "ex_date": str(action.ex_date),
                    "volume_ratio": round(vol_ratio, 4),
                    "expected_ratio": round(expected_ratio, 4),
                    "acceptable": abs(vol_ratio - expected_ratio) < 0.2,
                })

    all_ok = all(c.get("acceptable", True) for c in checks)
    return {
        "verified": all_ok,
        "checks": checks,
        "message": "All adjustments verified" if all_ok else "Some adjustments may be incorrect",
    }


def get_known_actions(symbol: str) -> list[CorporateAction]:
    """Get known corporate actions for a symbol (for testing)."""
    return KNOWN_ACTIONS.get(symbol.upper(), [])


def register_known_action(symbol: str, action: CorporateAction) -> None:
    """Register a known corporate action (for testing/historical data)."""
    sym = symbol.upper()
    if sym not in KNOWN_ACTIONS:
        KNOWN_ACTIONS[sym] = []
    KNOWN_ACTIONS[sym].append(action)
    KNOWN_ACTIONS[sym].sort(key=lambda a: a.ex_date)


__all__: Sequence[str] = (
    "CorporateActionType",
    "CorporateAction",
    "AdjustmentResult",
    "KNOWN_ACTIONS",
    "apply_corporate_actions",
    "verify_adjustment_correctness",
    "get_known_actions",
    "register_known_action",
)
