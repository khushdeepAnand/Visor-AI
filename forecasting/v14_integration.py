"""Resilient, point-in-time context and calibration state for v14 forecasts.

Forecast requests only *read* ACI state.  The settlement path must call
``record_realized_outcome`` after an authoritative outcome is known; pending,
manual, stale, or demo observations are rejected.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = "forecast-v14-context-1"
ACI_STATE_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 3.0
_STATE_LOCK = threading.RLock()
_BREAKER_LOCK = threading.RLock()
_BREAKERS: dict[str, dict[str, float | int]] = {}
_METRICS: dict[str, int] = {"attempted": 0, "available": 0, "degraded": 0, "timeouts": 0, "circuit_open": 0, "unavailable": 0}

#: Rollout knobs for the CQR challenger.  ``STOCKPILOT_CQR_CANARY_PERCENT`` is
#: the operational control (0..100); ``STOCKPILOT_CQR_CANARY_PCT`` is the
#: fraction form recorded on the promotion receipt (0..1).
CANARY_PERCENT_ENV = "STOCKPILOT_CQR_CANARY_PERCENT"
CANARY_FRACTION_ENV = "STOCKPILOT_CQR_CANARY_PCT"
DEFAULT_CANARY_PERCENT = 10.0


@dataclass(slots=True)
class ContextInput:
    name: str
    status: str
    value: Any = None
    source: str | None = None
    as_of: str | None = None
    reason: str | None = None
    rows: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _state_path() -> Path:
    configured = os.getenv("STOCKPILOT_ACI_STATE_PATH")
    return Path(configured) if configured else Path(__file__).resolve().parents[1] / "cache" / "forecast_v14" / "aci_state.json"


def _key(symbol: str, timeframe: str, horizon: int, confidence: float) -> str:
    return f"{symbol.strip().upper()}|{timeframe}|{int(horizon)}|{float(confidence):.4f}"


def _empty_state() -> dict[str, Any]:
    return {"version": ACI_STATE_VERSION, "updated_at": None, "states": {}, "processed_outcomes": []}


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != ACI_STATE_VERSION or not isinstance(payload.get("states"), dict):
            raise ValueError("unsupported ACI state version")
        payload.setdefault("processed_outcomes", [])
        return payload
    except FileNotFoundError:
        return _empty_state()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("forecast_v14_aci_state_unavailable", extra={"reason": type(exc).__name__})
        return _empty_state()


def read_aci_state(symbol: str, timeframe: str, horizon: int, confidence: float) -> dict[str, Any]:
    """Read state without updating it (forecast-time safe)."""
    with _STATE_LOCK:
        payload = _load_state()
    state = payload["states"].get(_key(symbol, timeframe, horizon, confidence), {})
    return {
        "schema_version": ACI_STATE_VERSION,
        "gamma": float(state.get("gamma", 0.0)),
        "step": int(state.get("step", 0)),
        "last_realized_at": state.get("last_realized_at"),
        "update_policy": "authoritative_realized_outcomes_only",
    }


def record_realized_outcome(
    *, symbol: str, timeframe: str, horizon: int, confidence: float,
    covered: bool, outcome_id: str, realized_at: str,
    official: bool, automatic: bool, is_stale: bool = False, is_demo: bool = False,
    learning_rate: float = 0.1,
) -> dict[str, Any]:
    """Persist one idempotent ACI update after an authoritative realization."""
    if not (official and automatic) or is_stale or is_demo:
        raise ValueError("ACI updates require an official, automatic, non-stale, non-demo realized outcome.")
    realized = pd.Timestamp(realized_at)
    if realized.tzinfo is None:
        realized = realized.tz_localize("UTC")
    else:
        realized = realized.tz_convert("UTC")
    if pd.isna(realized) or realized > pd.Timestamp.now(tz="UTC") + pd.Timedelta(minutes=5):
        raise ValueError("realized_at must identify an outcome already observed.")
    identifier = str(outcome_id).strip()
    if not identifier:
        raise ValueError("outcome_id is required for idempotency.")
    with _STATE_LOCK:
        payload = _load_state()
        if identifier in payload["processed_outcomes"]:
            return {**read_aci_state(symbol, timeframe, horizon, confidence), "updated": False}
        state_key = _key(symbol, timeframe, horizon, confidence)
        state = dict(payload["states"].get(state_key, {}))
        gamma = float(state.get("gamma", 0.0)) + float(learning_rate) * (float(bool(covered)) - float(confidence))
        state.update({"gamma": gamma, "step": int(state.get("step", 0)) + 1, "last_realized_at": realized.isoformat(), "last_outcome_id": identifier})
        payload["states"][state_key] = state
        payload["processed_outcomes"] = (payload["processed_outcomes"] + [identifier])[-10000:]
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    return {**read_aci_state(symbol, timeframe, horizon, confidence), "updated": True}


def _bounded(name: str, loader: Callable[[], Any], timeout: float) -> tuple[Any | None, str | None]:
    now = time.monotonic()
    with _BREAKER_LOCK:
        breaker = _BREAKERS.setdefault(name, {"failures": 0, "open_until": 0.0})
        if float(breaker["open_until"]) > now:
            _METRICS["circuit_open"] += 1
            return None, "circuit_open"
    _METRICS["attempted"] += 1
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"forecast-{name}")
    future = executor.submit(loader)
    try:
        value = future.result(timeout=max(0.05, float(timeout)))
        with _BREAKER_LOCK:
            breaker.update({"failures": 0, "open_until": 0.0})
        _METRICS["available"] += 1
        return value, None
    except FutureTimeout:
        future.cancel()
        _METRICS["timeouts"] += 1
        reason = "timeout"
    except Exception as exc:  # provider boundary
        reason = type(exc).__name__
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    with _BREAKER_LOCK:
        failures = int(breaker["failures"]) + 1
        breaker.update({"failures": failures, "open_until": time.monotonic() + 60.0 if failures >= 3 else 0.0})
    _METRICS["degraded"] += 1
    LOGGER.warning("forecast_v14_context_degraded", extra={"input": name, "reason": reason})
    return None, reason


def _align_frame(name: str, frame: Any, cutoff: pd.Timestamp) -> ContextInput:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return ContextInput(name, "unavailable", reason="provider returned no history")
    aligned = frame.copy()
    aligned.index = pd.to_datetime(aligned.index, errors="coerce")
    aligned = aligned[aligned.index.notna()].sort_index()
    index = pd.DatetimeIndex(aligned.index)
    cutoff_cmp = cutoff
    if index.tz is not None and cutoff_cmp.tzinfo is None:
        cutoff_cmp = cutoff_cmp.tz_localize(index.tz)
    elif index.tz is None and cutoff_cmp.tzinfo is not None:
        cutoff_cmp = cutoff_cmp.tz_localize(None)
    aligned = aligned.loc[aligned.index <= cutoff_cmp]
    if aligned.empty:
        return ContextInput(name, "unavailable", reason="no observation at or before forecast origin")
    as_of = pd.Timestamp(aligned.index[-1]).isoformat()
    return ContextInput(name, "available", value=aligned, source=str(frame.attrs.get("provider") or frame.attrs.get("source") or "provided"), as_of=as_of, rows=len(aligned))


def collect_context(
    *, symbol: str, origin: Any, timeframe: str, window: str,
    market_data: pd.DataFrame | None = None, vix_data: pd.DataFrame | None = None,
    history_loader: Callable[[str, str, str], pd.DataFrame] | None = None,
    ipo_info: dict[str, Any] | None = None,
    peer_universe: list[str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Collect optional context and truncate every series at the forecast origin."""
    cutoff = pd.Timestamp(origin)
    instrument: Any = None
    try:
        from services.market_data.instruments import CATALOGUE
        instrument = CATALOGUE.resolve(symbol)
        contracts = [item for item in CATALOGUE.load() if item.segment in {"NSE_FO", "BSE_FO"} and (item.underlying_symbol or "").upper() == symbol.upper()]
        fno = ContextInput("fno_membership", "available", value=bool(contracts), source="instrument_master", as_of=cutoff.isoformat(), rows=len(contracts))
    except Exception as exc:
        fno = ContextInput("fno_membership", "unavailable", reason=type(exc).__name__)

    inputs: dict[str, ContextInput] = {"fno": fno}
    supplied = {"market_index": market_data, "india_vix": vix_data}
    symbols = {"market_index": "NIFTY 50", "india_vix": "INDIA VIX"}
    for name in ("market_index", "india_vix"):
        frame = supplied[name]
        reason = None
        if frame is None and history_loader is not None:
            loader = history_loader

            def _load_history(name: str = name) -> pd.DataFrame:
                return loader(symbols[name], timeframe, window)

            frame, reason = _bounded(name, _load_history, timeout_seconds)
        if frame is None:
            inputs[name] = ContextInput(name, "unavailable", reason=reason or "no configured point-in-time provider input")
            _METRICS["unavailable"] += 1
        else:
            inputs[name] = _align_frame(name, frame, cutoff)
        LOGGER.info(
            "forecast_v14_context_step",
            extra={"input": name, "status": inputs[name].status, "rows": inputs[name].rows, "reason": inputs[name].reason},
        )

    listing = ContextInput("ipo", "unavailable", reason="authoritative listing/offer metadata is not exposed by the provider contract")
    if isinstance(ipo_info, dict) and ipo_info:
        # Caller-supplied metadata is accepted only as an explicit snapshot;
        # the caller remains responsible for its point-in-time provenance.
        listing = ContextInput("ipo", "available", value=dict(ipo_info), source="caller_supplied", as_of=str(ipo_info.get("as_of") or cutoff.isoformat()))
    if instrument is not None:
        listing.source = "instrument_master"
    LOGGER.info("forecast_v14_context_step", extra={"input": "ipo", "status": listing.status, "reason": listing.reason})
    peers = ContextInput("peers", "unavailable", reason="sector and point-in-time peer membership are not exposed by the instrument contract")
    if peer_universe:
        peers = ContextInput("peers", "available", value=[str(item).upper() for item in peer_universe], source="caller_supplied", as_of=cutoff.isoformat(), rows=len(peer_universe))
    inputs.update({"ipo": listing, "peers": peers})
    if peers.status != "available":
        _METRICS["unavailable"] += 1
    LOGGER.info("forecast_v14_context_step", extra={"input": "peers", "status": peers.status, "reason": peers.reason})
    lineage = {
        key: {k: v for k, v in item.to_dict().items() if k != "value"}
        for key, item in inputs.items()
    }
    return {"schema_version": SCHEMA_VERSION, "inputs": inputs, "lineage": lineage, "metrics": dict(_METRICS)}


def canary_percentage(promotion_receipt: dict[str, Any] | None = None) -> float:
    """Return the CQR rollout percentage in 0..100, resolved deterministically.

    Precedence: the ``STOCKPILOT_CQR_CANARY_PERCENT`` operational override
    (so an emergency rollback to zero needs no redeploy), then the receipt's
    ``canary_pct`` (the fraction the gate approved), then the
    ``STOCKPILOT_CQR_CANARY_PCT`` fraction form, then the default.  A
    malformed value resolves to ``0.0`` (fail closed), never to a silently
    wider rollout than an operator asked for.
    """
    raw = os.getenv(CANARY_PERCENT_ENV)
    if raw is not None:
        try:
            return min(100.0, max(0.0, float(raw)))
        except (TypeError, ValueError):
            return 0.0
    if isinstance(promotion_receipt, dict) and promotion_receipt.get("canary_pct") is not None:
        try:
            return min(100.0, max(0.0, float(promotion_receipt["canary_pct"]) * 100.0))
        except (TypeError, ValueError):
            return 0.0
    raw = os.getenv(CANARY_FRACTION_ENV)
    if raw is not None:
        try:
            return min(100.0, max(0.0, float(raw) * 100.0))
        except (TypeError, ValueError):
            return 0.0
    return DEFAULT_CANARY_PERCENT


def canary_bucket(key: str) -> float:
    """Stable 0..100 routing bucket for a request key.

    SHA-256 keeps the same symbol in the same arm across processes and
    restarts; Python's builtin ``hash`` does not (``PYTHONHASHSEED``).
    """
    digest = hashlib.sha256(str(key).upper().encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) % 10000 / 100.0


def cqr_canary_status(promotion_receipt: dict[str, Any] | None = None, *, symbol: str = "", has_residuals: bool = False) -> dict[str, Any]:
    """Deterministic, fail-closed CQR canary state.

    ``enabled`` records whether the promotion gate is active. Per-symbol
    rollout is reported separately by ``canary_assigned`` so control-arm
    forecasts remain distinguishable from an invalid promotion receipt.
    """
    required = {"passed", "gate_version", "evaluated_at", "artifact_hash", "n_forecasts", "coverage", "winkler_improved"}
    valid = bool(promotion_receipt and required.issubset(promotion_receipt) and promotion_receipt.get("passed") is True and int(promotion_receipt.get("n_forecasts", 0)) >= 100 and promotion_receipt.get("winkler_improved") is True)
    percentage = canary_percentage(promotion_receipt)
    bucket = canary_bucket(symbol) if symbol else 0.0
    assigned = bool(symbol) and bucket < percentage
    # CQR determines published bounds only when: promotion gate passed, 
    # symbol is assigned to canary arm, AND calibration residuals are available
    determines_bounds = bool(valid and assigned and has_residuals)
    return {
        "enabled": valid,
        "receipt_valid": valid,
        "status": "promoted_canary" if valid and assigned else ("control_arm" if valid else "disabled_pending_real_promotion_gate"),
        "canary_percentage": percentage,
        "canary_bucket": round(bucket, 2),
        "canary_assigned": assigned,
        "config_path": CANARY_PERCENT_ENV,
        "determines_published_bounds": determines_bounds,
        "required_gate_fields": sorted(required),
    }
