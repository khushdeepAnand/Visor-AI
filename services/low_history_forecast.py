"""Bridge between the API forecast path and the pooled cross-sectional model.

The per-symbol interval engine refuses instruments with too little history and
raises ``InsufficientDataError``. Before this module existed the request simply
failed with ``insufficient_history``, which is honest but unhelpful for newly
listed or thinly traded NSE/BSE names.

This bridge gives those instruments one clearly labelled alternative: the
validated pooled cross-sectional model in ``forecasting/pooled_cross_section.py``.
It is deliberately conservative:

* it runs only when the ``pooled_low_history_model`` feature flag is enabled for
  the caller, so it can be staged and rolled back like any other flagged change;
* it never substitutes for the production interval path when that path works;
* it returns the pooled model's own abstention when the evidence is insufficient,
  instead of inventing a range;
* every payload states plainly that the range came from a cross-instrument model
  rather than from the symbol's own history.

History loading and peer selection are injected, so this module is testable
without any provider, network access, or database.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from forecasting.pooled_cross_section import (
    MIN_INSTRUMENTS,
    POOLED_MODEL_VERSION,
    PooledModelError,
    build_pooled_dataset,
    dataset_report,
    fit_pooled_forecaster,
    latest_feature_row,
)

try:  # pragma: no cover - exercised indirectly through the API
    from services.forecast_guardrails import GUARDRAILS
except Exception:  # pragma: no cover - keeps the module importable standalone
    GUARDRAILS = None  # type: ignore[assignment]

#: Feature flag that gates this whole path. Declared in forecast_guardrails.
LOW_HISTORY_FLAG = "pooled_low_history_model"

#: Public label. Never reuse the production model label for pooled output.
POOLED_MODEL_LABEL = "pooled cross-instrument research model"

#: Hard ceiling on peers pulled per request, so one forecast cannot fan out into
#: an unbounded number of provider history calls.
MAX_PEERS = 30

#: Default peer universe: large, continuously traded NSE equities used only as
#: pooled training companions. This is a deliberately fixed, reviewable list
#: rather than a live index download, so a pooled fit cannot silently change
#: shape between requests. It is India-only by design.
DEFAULT_PEER_UNIVERSE: tuple[str, ...] = (
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "LT",
    "KOTAKBANK", "AXISBANK", "ASIANPAINT", "MARUTI", "BAJFINANCE",
    "HCLTECH", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO",
    "NESTLEIND", "POWERGRID", "NTPC", "TATAMOTORS", "TATASTEEL",
    "JSWSTEEL", "TECHM", "GRASIM", "CIPLA", "COALINDIA",
)

#: Broad-market reference used for the market-relative features. Loading it is
#: best effort: if the provider cannot serve it, the pooled fit continues
#: without market-relative context instead of failing the request.
POOLED_MARKET_SYMBOL = "NIFTY 50"

HistoryLoader = Callable[[str], pd.DataFrame]


class LowHistoryUnavailable(RuntimeError):
    """Raised when the pooled path cannot serve this request.

    Carries a stable ``code`` so the API layer can keep returning documented
    forecast contract codes instead of a generic failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)


def _normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Accept the application's OHLCV frame and return the pooled model's shape.

    The market-data manager returns title-case columns on a DatetimeIndex; the
    pooled model expects lowercase columns plus an explicit ``date`` column. No
    row is added, removed, resampled, or filled here.
    """
    if frame is None or len(frame) == 0:
        raise LowHistoryUnavailable("history_unavailable", "No market history is available for this instrument.")
    working = frame.copy()
    renamed = {str(column): str(column).strip().lower() for column in working.columns}
    working = working.rename(columns=renamed)
    if "date" not in working.columns:
        index = pd.to_datetime(pd.Series(working.index, index=working.index), errors="coerce")
        working = working.assign(date=index.values)
    required = ("open", "high", "low", "close", "volume")
    missing = [name for name in required if name not in working.columns]
    if missing:
        raise LowHistoryUnavailable(
            "history_unavailable",
            f"History for this instrument is missing required columns: {', '.join(missing)}.",
        )
    return working.loc[:, ["date", *required]].reset_index(drop=True)


def _flag_enabled(subject: str | None, override: Callable[[], bool] | None) -> bool:
    if override is not None:
        return bool(override())
    if GUARDRAILS is None:  # pragma: no cover - only when imported standalone
        return False
    return bool(GUARDRAILS.is_feature_enabled(LOW_HISTORY_FLAG, subject=subject))


def _collect_panels(
    symbol: str,
    peer_symbols: Iterable[str],
    history_loader: HistoryLoader,
    *,
    max_peers: int,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Load the target plus its peers, recording every skipped instrument."""
    target = str(symbol).strip().upper()
    panels: dict[str, pd.DataFrame] = {}
    skipped: dict[str, str] = {}

    try:
        panels[target] = _normalize_ohlcv(history_loader(target))
    except LowHistoryUnavailable:
        raise
    except Exception as error:
        # A provider gap on the requested instrument is a documented refusal,
        # not a crash: the caller maps this code to a user-facing message.
        raise LowHistoryUnavailable(
            "history_unavailable",
            f"History for {target} could not be loaded: {str(error)[:160]}",
        ) from error

    seen = {target}
    for peer in peer_symbols:
        key = str(peer).strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        if len(panels) > max_peers:
            break
        try:
            panels[key] = _normalize_ohlcv(history_loader(key))
        except Exception as error:  # provider gaps must not fail the request
            skipped[key] = str(error)[:160]
    return panels, skipped


def pooled_low_history_forecast(
    symbol: str,
    *,
    timeframe: str,
    training_window: str,
    confidence: float = 0.80,
    history_loader: HistoryLoader,
    peer_symbols: Sequence[str],
    market_symbol: str | None = None,
    horizon: int = 5,
    subject: str | None = None,
    flag_override: Callable[[], bool] | None = None,
    max_peers: int = MAX_PEERS,
) -> dict[str, Any]:
    """Produce a pooled research range, or raise :class:`LowHistoryUnavailable`.

    The returned payload deliberately mirrors the published forecast contract
    (``support_state``, ``research_range``, ``evidence``, ``disclaimer``) so the
    existing UI can render it with the same designed states, while the
    ``model_label`` and ``disclosure`` make the different provenance explicit.
    """
    target = str(symbol).strip().upper()
    if not _flag_enabled(subject, flag_override):
        raise LowHistoryUnavailable(
            "pooled_model_disabled",
            "The cross-instrument research model is not enabled for this deployment.",
        )

    panels, skipped = _collect_panels(target, peer_symbols, history_loader, max_peers=max_peers)
    market_frame: pd.DataFrame | None = None
    if market_symbol:
        try:
            market_frame = _normalize_ohlcv(history_loader(str(market_symbol)))
        except Exception:
            market_frame = None

    if len(panels) < MIN_INSTRUMENTS:
        raise LowHistoryUnavailable(
            "pooled_peers_insufficient",
            (
                f"A cross-instrument fit needs at least {MIN_INSTRUMENTS} instruments with usable "
                f"history; only {len(panels)} were available."
            ),
        )

    try:
        pooled = build_pooled_dataset(panels, market=market_frame, horizon=horizon)
        forecaster = fit_pooled_forecaster(pooled)
        row, freshness = latest_feature_row(target, panels[target], market=market_frame, horizon=horizon)
    except PooledModelError as error:
        raise LowHistoryUnavailable("pooled_model_unavailable", str(error)) from error

    reference_price = float(panels[target]["close"].iloc[-1])
    try:
        result = forecaster.forecast_interval(
            reference_price=reference_price,
            features=row,
            sessions_available=int(freshness["sessions_available"]),
            confidence=float(confidence),
            median_turnover=freshness.get("median_turnover"),
        )
    except PooledModelError as error:
        raise LowHistoryUnavailable("pooled_model_unavailable", str(error)) from error

    report = dataset_report(pooled)
    state = str(result.get("state", "abstained"))
    tier = str(result.get("evidence_tier", "none"))
    payload: dict[str, Any] = {
        "symbol": target,
        "forecast_path": "pooled_cross_sectional",
        "model_label": POOLED_MODEL_LABEL,
        "model_version": POOLED_MODEL_VERSION,
        "support_state": state,
        "abstained": state == "abstained",
        "timeframe": timeframe,
        "training_window": training_window,
        "horizon": {"bars": int(result.get("horizon_bars", horizon))},
        "evidence": {
            "grade": tier if tier in {"A", "B", "C"} else "none",
            "summary": (
                "Evidence comes from cross-instrument validation (leave-instrument-out and "
                "forward-time folds), not from this symbol's own history."
            ),
            "detail": result.get("evidence"),
            "cohort": freshness.get("cohort"),
            "supported_horizons": result.get("supported_horizons", []),
        },
        "freshness": freshness,
        "peers": {
            "used": report["symbols"],
            "excluded": report["excluded_instruments"],
            "unavailable": skipped,
            "count": len(report["symbols"]),
        },
        "reference_price": round(reference_price, 2),
        "disclosure": result.get(
            "disclosure",
            "This range comes from a model trained across many instruments, not from this symbol alone.",
        ),
    }

    if state == "abstained":
        payload["code"] = str(result.get("code", "insufficient_evidence"))
        payload["message"] = str(result.get("message", "No range is published for this instrument."))
        payload["research_range"] = None
        return payload

    payload["research_range"] = {
        "low": result["low"],
        "median_reference": result["median"],
        "high": result["high"],
        "confidence_level": result["nominal_coverage"],
        "currency": "INR",
    }
    payload["uncertainty"] = {
        "range_width": result["width_points"],
        "range_width_pct": result["width_pct_of_price"],
    }
    payload["low_utility"] = state == "low_utility"
    payload["expected_return"] = result["expected_return"]
    payload["shrinkage_weight"] = result["shrinkage_weight"]
    return payload


def low_history_status(subject: str | None = None) -> dict[str, Any]:
    """Small, non-privileged description of this path for the system view."""
    return {
        "flag": LOW_HISTORY_FLAG,
        "enabled": _flag_enabled(subject, None),
        "model_label": POOLED_MODEL_LABEL,
        "model_version": POOLED_MODEL_VERSION,
        "minimum_instruments": MIN_INSTRUMENTS,
        "note": (
            "Used only when a symbol has too little history for the per-symbol interval engine. "
            "Abstention is a valid outcome."
        ),
    }


__all__: Sequence[str] = (
    "DEFAULT_PEER_UNIVERSE",
    "LOW_HISTORY_FLAG",
    "MAX_PEERS",
    "POOLED_MARKET_SYMBOL",
    "POOLED_MODEL_LABEL",
    "LowHistoryUnavailable",
    "low_history_status",
    "pooled_low_history_forecast",
)
