"""Corporate-action registry and backward OHLC adjustment (Part L2).

The dossier ranks ignorant corporate actions as "the single most common
source of silently wrong Indian equity history". This module provides the
missing adjustment stage: a file-backed registry of verified events whose
ratios are applied to historical OHLC so a split or bonus does not look like
a crash or a spike.

The registry ships empty on purpose. Event entries are only added from a
named source (a subscription, a corporate-action feed, or manual
verification); nothing here invents adjustments. The pipeline is live the
moment an operator adds a row.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

logger = logging.getLogger("stockpilot.corporate_actions")

OHLC = ("Open", "High", "Low", "Close")

_REGISTRY_FILE = Path(__file__).resolve().parent.parent.parent / "market_data" / "corporate_actions.json"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    symbol: str
    event_type: str
    effective_date: date
    ratio: float
    note: str = ""


def _parse_row_date(value: str) -> date:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def load_registry(path: str | Path | None = None) -> list[CorporateAction]:
    """Load the registry file; a missing or corrupt file yields [] (never raises)."""
    registry_path = Path(path) if path is not None else _REGISTRY_FILE
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Corporate-action registry not readable at %s: %s; treating as empty.", registry_path, error)
        return []
    rows = raw.get("corporate_actions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        logger.warning("Corporate-action registry %s has no 'corporate_actions' list.", registry_path)
        return []
    actions: list[CorporateAction] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            symbol = str(row.get("symbol") or "").strip().upper()
            event_type = str(row.get("event_type") or "").strip()
            effective_date = _parse_row_date(str(row.get("effective_date") or "").strip())
            raw_ratio = row.get("ratio")
            if raw_ratio is None:
                raise ValueError("missing ratio")
            ratio = float(raw_ratio)
            if not symbol or not event_type or ratio <= 0:
                raise ValueError("incomplete row")
            if event_type not in {"split", "bonus", "demerger", "reverse_split"}:
                raise ValueError("unsupported event type")
            actions.append(CorporateAction(symbol, event_type, effective_date, ratio, str(row.get("note") or "")[:200]))
        except (TypeError, ValueError) as error:
            logger.warning("Ignoring malformed corporate action row %r: %s", row, error)
    return actions


def registered_actions_for(symbol: str, *, path: str | Path | None = None) -> list[CorporateAction]:
    """Return the verified events for ``symbol`` ordered by effective date."""
    normalized = str(symbol or "").strip().upper()
    actions = sorted(
        (action for action in load_registry(path) if action.symbol == normalized),
        key=lambda action: action.effective_date,
    )
    return actions


def apply_corporate_actions(frame: pd.DataFrame, events: Iterable[CorporateAction]) -> tuple[pd.DataFrame, list[str]]:
    """Backward-adjust OHLC/volume for each event; returns (frame, applied_labels).

    Rows strictly before an event's effective date are scaled by ``1/ratio``
    (prices) and ``ratio`` (volume) so the series is continuous in pre-event
    terms. Multiple events compose correctly because masks are applied
    ascending by date.
    """
    actions = sorted(events, key=lambda action: action.effective_date)
    if not actions or frame is None or frame.empty:
        return frame, []
    adjusted = frame.copy()
    index = pd.to_datetime(adjusted.index)
    labels: list[str] = []
    for action in actions:
        effective = pd.Timestamp(action.effective_date)
        before = index < effective
        if not before.any():
            continue
        price_scale = 1.0 / action.ratio
        for column in OHLC:
            if column in adjusted.columns:
                adjusted.loc[before, column] = adjusted.loc[before, column] * price_scale
        if "Volume" in adjusted.columns:
            adjusted.loc[before, "Volume"] = adjusted.loc[before, "Volume"] * action.ratio
        labels.append(
            f"{action.event_type}@{action.effective_date.isoformat()}:{action.ratio:g}"
        )
    return adjusted, labels


def adjust_history_frame(frame: pd.DataFrame, symbol: str, *, path: str | Path | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Convenience used by ``normalize_ohlcv``: registry lookup + apply."""
    return apply_corporate_actions(frame, registered_actions_for(symbol, path=path))