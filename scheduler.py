"""Background maintenance jobs for the StockPilot AI v6 FastAPI service.

Jobs deliberately use the same provider + range-forecast contracts as the API.
No Streamlit or global-market ticker path exists here.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from forecasting.interval_forecast import DEFAULT_HORIZONS, WINDOW_TIMEFRAME_DEFAULTS, forecast_range
from forecasting.model_promotion import active_promotion_receipt
from forecasting.live_decay import build_symbol_decay_status
from services.market_data.instruments import CATALOGUE
from services.market_data.manager import MANAGER
from services.market_data.reconcile_instruments import reconcile_instrument_master
from services.outcome_settlement import settle_due_forecasts
from services.retention import run_retention_enforcement
from services.alerts import deliver_triggered_alerts, evaluate_price_alerts, list_price_alerts
from services.alert_transport import dispatch_operational_alert
from services.otel_instrumentation import (
    trace_reconciliation_operation,
    trace_forecast_operation,
    trace_provider_call,
    trace_market_data_operation,
)
from services.scorecard import build_public_scorecard
from database import get_connection, get_settled_rows_for_quality

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
REFRESH_DIR = ROOT / "cache" / "model_refresh"
REFRESH_DIR.mkdir(parents=True, exist_ok=True)


def _symbols() -> list[str]:
    configured = os.getenv("STOCKPILOT_RETRAIN_SYMBOLS", "RELIANCE,TCS,INFY,NIFTY 50")
    accepted: list[str] = []
    for raw in configured.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            accepted.append(MANAGER.normalize_symbol(value))
        except ValueError:
            LOGGER.warning("Skipping non-catalogue scheduled symbol: %s", value)
    return accepted


def refresh_instrument_master() -> None:
    """Refresh the India-only NSE/BSE BOD instrument master."""
    try:
        result = CATALOGUE.refresh_from_upstox()
        LOGGER.info("Instrument master refresh complete: %s rows", result.get("rows"))
    except Exception:
        LOGGER.exception("Scheduled instrument-master refresh failed")
        return
    try:
        report = reconcile_instrument_master(CATALOGUE)
        LOGGER.info(
            "Instrument reconciliation: recommended_action=%s expected=%s resolved=%s missing=%s extra=%s",
            report.get("recommended_action"),
            report.get("expected"),
            report.get("resolved"),
            report.get("missing"),
            report.get("extra"),
        )
    except Exception:
        LOGGER.exception("Scheduled instrument reconciliation failed")


def retrain_configured_symbols() -> None:
    """Retrain/validate range forecasts and persist the latest job result.

    The production forecast function trains chronologically on demand. This job
    proactively exercises the same training path after market close so drift and
    interval-quality metrics are available before the next session.
    """
    window = os.getenv("STOCKPILOT_RETRAIN_WINDOW", "1y").strip()
    if window not in WINDOW_TIMEFRAME_DEFAULTS:
        window = "1y"
    timeframe = os.getenv("STOCKPILOT_RETRAIN_TIMEFRAME", WINDOW_TIMEFRAME_DEFAULTS[window]).strip()
    confidence = float(os.getenv("STOCKPILOT_RETRAIN_CONFIDENCE", "0.80"))
    for symbol in _symbols():
        try:
            with trace_market_data_operation("get_history", symbol, timeframe=timeframe, window=window):
                history = MANAGER.get_history(symbol, timeframe=timeframe, window=window)
            with trace_forecast_operation("range_forecast", symbol, timeframe=timeframe, window=window, confidence=confidence):
                result = forecast_range(
                    symbol,
                    history,
                    confidence_level=confidence,
                    training_window=window,
                    timeframe=timeframe,
                    horizons=DEFAULT_HORIZONS,
                    cqr_promotion_receipt=active_promotion_receipt(),
                )
            path = REFRESH_DIR / f"{symbol.replace(' ', '_').replace('/', '_')}.json"
            path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
            LOGGER.info("Scheduled range-model refresh complete for %s", symbol)
        except Exception:
            LOGGER.exception("Scheduled range-model refresh failed for %s", symbol)


def warm_market_cache() -> None:
    """Warm configured symbols through the provider manager after market open."""
    for symbol in _symbols():
        try:
            with trace_provider_call("upstox", "get_quote", symbol):
                MANAGER.get_quote(symbol)
        except Exception:
            LOGGER.exception("Quote cache warm failed for %s", symbol)


def settle_due_forecast_outcomes() -> None:
    """Finalize due forecast ledger rows from authoritative observations."""
    try:
        result = settle_due_forecasts()
        LOGGER.info("Scheduled forecast outcome settlement complete: %s", result)
    except Exception:
        LOGGER.exception("Scheduled forecast outcome settlement failed")


def evaluate_model_drift_and_retrain() -> None:
    """Score model health from settled outcomes and auto-retrain drift groups.

    Runs after settlement. Each (symbol,timeframe,horizon) group with enough
    settled evidence is scored (rolling coverage, MASE vs persistence,
    directional accuracy). Groups that fail the drift rules trigger a retrain
    through the production forecast function and an audit row in
    ``model_health`` with ``action_taken='auto_retrained'``. The refresh
    artifact lands in ``REFRESH_DIR`` so the UI can show when a symbol was last
    refreshed. Failures are isolated per group.
    """
    from database import get_settled_rows_for_quality, record_model_health
    from forecasting.drift_monitor import quality_dashboard

    try:
        dashboard = quality_dashboard(get_settled_rows_for_quality())
    except Exception:
        LOGGER.exception("Model-health evaluation failed before grouping")
        return

    window = os.getenv("STOCKPILOT_RETRAIN_WINDOW", "1y").strip()
    if window not in WINDOW_TIMEFRAME_DEFAULTS:
        window = "1y"
    timeframe = os.getenv("STOCKPILOT_RETRAIN_TIMEFRAME", WINDOW_TIMEFRAME_DEFAULTS[window]).strip()
    confidence = float(os.getenv("STOCKPILOT_RETRAIN_CONFIDENCE", "0.80"))

    for group in dashboard["groups"]:
        symbol = str(group.get("symbol") or "").strip().upper()
        try:
            action_taken = None
            if group.get("drift_detected"):
                action_taken = "auto_retrained"
                try:
                    with trace_market_data_operation("get_history", symbol, timeframe=timeframe, window=window):
                        history = MANAGER.get_history(symbol, timeframe=timeframe, window=window)
                    with trace_forecast_operation("drift_autoretrain", symbol, timeframe=timeframe, window=window, confidence=confidence, horizon=group.get("horizon")):
                        forecast_range(
                            symbol,
                            history,
                            confidence_level=confidence,
                            training_window=window,
                            timeframe=timeframe,
                            horizons=DEFAULT_HORIZONS,
                            cqr_promotion_receipt=active_promotion_receipt(),
                        )
                    path = REFRESH_DIR / f"auto_retrain_{symbol.replace(' ', '_').replace('/', '_')}.json"
                    path.write_text(
                        json.dumps(
                            {
                                "symbol": symbol,
                                "trigger": "drift_detected",
                                "horizon": group.get("horizon"),
                                "drift_reasons": group.get("drift_reasons") or [],
                                "retrained_at": datetime.now(timezone.utc).isoformat(),
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    LOGGER.info("Drift auto-retrain complete for %s (h=%s)", symbol, group.get("horizon"))
                except Exception:
                    LOGGER.exception("Drift auto-retrain failed for %s", symbol)
                    action_taken = None
            record_model_health(
                symbol=symbol,
                timeframe=str(group.get("timeframe") or ""),
                horizon=int(group.get("horizon") or 1),
                settled_samples=int(group.get("settled_samples") or 0),
                rolling_coverage=group.get("rolling_coverage"),
                nominal_coverage=group.get("nominal_coverage"),
                coverage_gap=group.get("coverage_gap"),
                mase=group.get("mase"),
                directional_accuracy=group.get("directional_accuracy"),
                naive_directional_accuracy=group.get("naive_directional_accuracy"),
                model_mae=group.get("model_mae"),
                naive_mae=group.get("naive_mae"),
                drift_detected=bool(group.get("drift_detected")),
                drift_reasons=group.get("drift_reasons") or [],
                severity=group.get("severity"),
                action_taken=action_taken,
            )
        except Exception:
            LOGGER.exception("Model-health record failed for %s", symbol)


def evaluate_scheduled_alerts() -> None:
    """Evaluate active spot alerts in the background and deliver opted-in email."""
    connection = get_connection()
    try:
        users = connection.execute(
            "SELECT DISTINCT u.id,u.email FROM users u JOIN price_alerts a ON a.user_id=u.id WHERE a.is_active=1"
        ).fetchall()
    finally:
        connection.close()
    for user_id, email in users:
        try:
            prices: dict[str, float] = {}
            for alert in list_price_alerts(int(user_id), active_only=True):
                if alert["condition"] in {"ABOVE", "BELOW"}:
                    prices[alert["symbol"]] = MANAGER.get_quote(alert["symbol"]).price
            triggered = evaluate_price_alerts(int(user_id), prices, {})
            if triggered:
                deliver_triggered_alerts(int(user_id), str(email or ""), triggered)
        except Exception:
            LOGGER.exception("Scheduled alert evaluation failed for a user")


def ingest_sentiment_snapshots() -> None:
    """Record a daily [-1, +1] sentiment snapshot per configured symbol.

    Feeds the K4 sentiment trend chart. Uses the same RSS + scoring path as the
    API; failures are isolated per symbol so one bad feed cannot abort the run.
    """
    from services.sentiment_snapshot import capture_snapshot

    for symbol in _symbols():
        try:
            captured = capture_snapshot(symbol)
            LOGGER.info(
                "Sentiment snapshot %s: avg=%s scored=%s/%s",
                captured["symbol"],
                captured["avg_score"],
                captured["scored_count"],
                captured["headline_count"],
            )
        except Exception:
            LOGGER.exception("Scheduled sentiment snapshot failed for %s", symbol)


def enforce_scheduled_retention() -> None:
    """Purge allowlisted stale records and artifacts under bounded policies."""
    try:
        result = run_retention_enforcement()
        LOGGER.info("Scheduled retention enforcement complete: counts=%s", result["counts"])
    except Exception:
        LOGGER.exception("Scheduled retention enforcement failed")


def compute_live_decay_for_all() -> None:
    """Enforce sustained tier decay using authoritative automatic settlements."""
    from database import get_settled_rows_for_quality
    from forecasting.live_decay import enforce_tier_decay
    try:
        actions = enforce_tier_decay(get_settled_rows_for_quality(limit=10000))
        LOGGER.info("Tier live-decay windows processed: %d", len(actions))
    except Exception:
        LOGGER.exception("Tier live-decay enforcement failed")


def backup_databases() -> None:
    """Scheduled encrypted backups include the signed promotion/control sidecar."""
    import database
    from forecasting.model_promotion import manifest_path
    from forecasting.promotion_store import registry_path
    from services.encrypted_storage import backup_and_rehearse
    key = database._get_encryption_key()
    sources = [Path(database.DATABASE)]
    registry = registry_path(manifest_path())
    if registry.exists():
        sources.append(registry)
    result = backup_and_rehearse(sources, Path(os.getenv("STOCKPILOT_BACKUP_DIR", str(ROOT / "backups"))),
                                os.getenv("STOCKPILOT_BACKUP_SECRET", ""),
                                database_key=key.decode() if key else None)
    LOGGER.info("Encrypted backup/restore drills complete: %d databases", len(result["backups"]))


def reconcile_challenger() -> None:
    """Run CQR challenger reconciliation against the fresh promotion gate.
    
    This job fetches the latest promotion gate artifacts and challenger results,
    compares coverage, and alerts if divergence exceeds tolerance.
    """
    import subprocess
    import json
    from pathlib import Path
    
    artifacts_dir = ROOT / "artifacts"
    gate_path = artifacts_dir / "fresh-gate.json"
    challenger_path = artifacts_dir / "cqr-challenger.json"
    
    with trace_reconciliation_operation("challenger", gate_path=str(gate_path), challenger_path=str(challenger_path)):
        if not gate_path.is_file() or not challenger_path.is_file():
            msg = "reconciliation artifacts not found"
            LOGGER.warning("Challenger reconciliation skipped: %s", msg)
            dispatch_operational_alert("coverage_divergence", msg)
            return

        try:
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            challenger = json.loads(challenger_path.read_text(encoding="utf-8"))
        except Exception as exc:
            msg = f"failed to load reconciliation artifacts: {exc}"
            LOGGER.error("ALERT coverage_divergence: %s", msg)
            dispatch_operational_alert("coverage_divergence", msg)
            return

        try:
            gc, cc = float(gate["coverage"]), float(challenger["coverage"])
            gq, cq = bool(gate["quantile_order_valid"]), bool(challenger["quantile_order_valid"])
            if not gq or not cq:
                msg = "quantile order invalid (possible inverted interval)"
                LOGGER.error("ALERT coverage_divergence: %s", msg)
                dispatch_operational_alert("coverage_divergence", msg)
                return
            tolerance = 0.02
            if abs(gc - cc) > tolerance:
                msg = f"coverage divergence {abs(gc-cc):.4f} > {tolerance:.4f}"
                LOGGER.error("ALERT coverage_divergence: %s", msg)
                dispatch_operational_alert("coverage_divergence", msg, details={"gate_coverage": gc, "challenger_coverage": cc})
            else:
                LOGGER.info("CQR challenger reconciliation passed: gate and challenger reconciled")
        except (KeyError, TypeError, ValueError) as exc:
            msg = f"coverage/order fields missing or invalid: {exc}"
            LOGGER.error("ALERT coverage_divergence: %s", msg)
            dispatch_operational_alert("coverage_divergence", msg)


def rebuild_public_scorecard() -> None:
    """Rebuild and cache the public scorecard."""
    try:
        scorecard = build_public_scorecard()
        
        # Cache to file for fast serving
        cache_dir = ROOT / "cache" / "scorecard"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / "public_scorecard.json"
        cache_path.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")
        
        LOGGER.info("Public scorecard rebuilt: %d tiers, %d total forecasts",
                    len(scorecard.get("tiers", {})), scorecard.get("overall", {}).get("total_forecasts", 0))
    except Exception:
        LOGGER.exception("Public scorecard rebuild failed")


def create_retention_scheduler() -> Any:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as error:
        raise RuntimeError("Install APScheduler to enable scheduled retention.") from error

    scheduler = BackgroundScheduler(timezone=os.getenv("STOCKPILOT_SCHEDULER_TIMEZONE", "Asia/Kolkata"))
    scheduler.add_job(
        enforce_scheduled_retention,
        "cron",
        hour=int(os.getenv("STOCKPILOT_RETENTION_HOUR", "2")),
        minute=int(os.getenv("STOCKPILOT_RETENTION_MINUTE", "15")),
        id="stockpilot-retention-enforcement",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(backup_databases, "cron", hour=2, minute=15, id="stockpilot-encrypted-backups",
                      replace_existing=True, coalesce=True, max_instances=1)
    return scheduler


def create_scheduler() -> Any:
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError as error:
        raise RuntimeError("Install APScheduler to enable scheduled maintenance.") from error

    timezone_name = os.getenv("STOCKPILOT_SCHEDULER_TIMEZONE", "Asia/Kolkata")
    scheduler = BackgroundScheduler(timezone=timezone_name)
    scheduler.add_job(
        settle_due_forecast_outcomes,
        "interval",
        minutes=max(1, int(os.getenv("STOCKPILOT_SETTLEMENT_INTERVAL_MINUTES", "15"))),
        id="stockpilot-forecast-outcome-settlement",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        evaluate_model_drift_and_retrain,
        "interval",
        minutes=max(2, int(os.getenv("STOCKPILOT_DRIFT_EVALUATION_INTERVAL_MINUTES", "20"))),
        id="stockpilot-drift-evaluation-autoretrain",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        evaluate_scheduled_alerts,
        "interval",
        minutes=max(1, int(os.getenv("STOCKPILOT_ALERT_EVALUATION_INTERVAL_MINUTES", "5"))),
        id="stockpilot-alert-evaluation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(refresh_instrument_master, "cron", hour=8, minute=0, id="stockpilot-instrument-master", replace_existing=True, coalesce=True, max_instances=1)
    scheduler.add_job(warm_market_cache, "cron", day_of_week="mon-fri", hour=9, minute=13, id="stockpilot-market-warmup", replace_existing=True, coalesce=True, max_instances=1)
    scheduler.add_job(
        ingest_sentiment_snapshots,
        "cron",
        day_of_week="mon-fri",
        hour=int(os.getenv("STOCKPILOT_SENTIMENT_HOUR", "17")),
        minute=int(os.getenv("STOCKPILOT_SENTIMENT_MINUTE", "30")),
        id="stockpilot-sentiment-snapshots",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        retrain_configured_symbols,
        "cron",
        day_of_week="mon-fri",
        hour=int(os.getenv("STOCKPILOT_RETRAIN_HOUR", "18")),
        minute=int(os.getenv("STOCKPILOT_RETRAIN_MINUTE", "30")),
        id="stockpilot-daily-model-refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        compute_live_decay_for_all,
        "interval",
        minutes=max(5, int(os.getenv("STOCKPILOT_DECAY_INTERVAL_MINUTES", "30"))),
        id="stockpilot-live-decay-computation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        rebuild_public_scorecard,
        "cron",
        day_of_week="mon-fri",
        hour=int(os.getenv("STOCKPILOT_SCORECARD_HOUR", "19")),
        minute=int(os.getenv("STOCKPILOT_SCORECARD_MINUTE", "0")),
        id="stockpilot-public-scorecard-rebuild",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        reconcile_challenger,
        "cron",
        day_of_week="mon-fri",
        hour=int(os.getenv("STOCKPILOT_RECONCILIATION_HOUR", "19")),
        minute=int(os.getenv("STOCKPILOT_RECONCILIATION_MINUTE", "30")),
        id="stockpilot-challenger-reconciliation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
