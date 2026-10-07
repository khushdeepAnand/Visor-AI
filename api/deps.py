"""Shared infrastructure for StockPilot AI v6 (split from api/main.py).

Routers import this module with ``from api.deps import *``. The wildcard is
bounded by __all__ which lists every top-level public name, module import
alias, and underscore-prefixed helper so moved handlers resolve their
dependencies exactly as they did inside the monolithic api/main.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from services.secure_secrets import load_windows_secure_secrets
from services.otel_instrumentation import (
    trace_forecast_operation,
    trace_provider_call,
    trace_market_data_operation,
)

load_windows_secure_secrets()
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from analytics.backtest_engine import run_moving_average_strategy, run_rsi_strategy
from analytics.pattern_engine import detect_chart_patterns
from analytics.risk_engine import calculate_performance_metrics, calculate_position_size, monte_carlo_projection, price_returns
from database import (
    add_to_watchlist,
    buy_stock,
    create_tables,
    database_health_check,
    delete_stock,
    get_audit_events,
    get_connection,
    get_portfolio,
    get_forecast_outcome_counts,
    get_prediction_details,
    get_settled_range_forecasts,
    get_settled_rows_for_quality,
    latest_model_health_by_group,
    get_transactions,
    get_watchlist,
    remove_from_watchlist,
    save_range_forecast,
    sell_stock,
    update_stock,
)
from derivatives.expiry_calendar import get_expiries
from derivatives.futures_engine import analyze_futures_contract, expiry_bounded_scenario_interval
from derivatives.options_engine import analyze_option_chain, binomial_greeks, binomial_tree, black_scholes, implied_volatility
from forecasting.interval_forecast import DEFAULT_HORIZONS, TRAINING_WINDOWS, WINDOW_TIMEFRAME_DEFAULTS, forecast_range
from forecasting.pooled_cross_section import select_ipo_peers
from forecasting.model_promotion import active_promotion_receipt
from derivatives.payoff import PayoffError, build_payoff
from services.morning_brief import BriefUnavailable, build_morning_brief
from forecasting.conformal_calibration import (
    CONFIDENCE_LEVELS,
    CalibrationError,
    calibration_report,
    describe_methodology as describe_calibration_methodology,
)
from services.setup_doctor import run_diagnostics as run_setup_diagnostics
from services.strategy_builder import (
    STRATEGIES,
    STRATEGY_FLAG,
    StrategyError,
    compile_strategy,
    describe_metrics,
    describe_operators,
    run_strategy,
    starter_strategies,
)
from services.strategy_backtest import (
    EQUITY_BACKTEST_FLAG,
    MULTI_LEG_FLAG,
    BacktestError,
    backtest_multi_leg,
    backtest_strategy,
)
from services.forward_test import FORWARD_TEST_FLAG, FORWARD_TESTS, ForwardTestError
from services.screener import SCREENS, ScreenerError, describe_fields, run_screen
from services.chart_layouts import CHART_LAYOUTS, ChartLayoutError
from services.sector_rotation import sector_rotation as _compute_sector_rotation
from forecasting.calibration_monitor import group_by_horizon, group_interval_quality
from indicators import add_indicators
from prediction import InsufficientDataError
from middleware.observability import API_METRICS, RateLimitMiddleware, RequestContextMiddleware
from middleware.security import CsrfOriginMiddleware, SecurityHeadersMiddleware, normalize_http_origin, parse_cors_origins
from services.alerts import FORECAST_CONDITIONS, create_price_alert, delete_price_alert, deliver_triggered_alerts, evaluate_price_alerts, list_price_alerts, set_alert_active
from services.admin_registry import (
    AdminConfigurationError,
    MAX_ADMINS,
    admin_audit_events,
    aggregate_counts,
    bootstrap_admins,
    configured_admin_emails,
    describe_settings,
    get_settings,
    is_admin_email,
    record_admin_action,
    update_settings,
)
from services.error_registry import error_groups, error_summary, record_error
from services.error_registry import reset as reset_error_groups
from services.admin_step_up import STEP_UP, STEP_UP_ACTIONS, StepUpConfigurationError, StepUpError
from services.security_center import run_security_checks
from services.forecast_guardrails import (
    GUARDRAILS,
    ForecastRequestContext,
    GuardrailError,
    enabled_features,
    forecast_block_reason,
    public_status_payload,
)
from services.low_history_forecast import (
    DEFAULT_PEER_UNIVERSE,
    LOW_HISTORY_FLAG,
    POOLED_MARKET_SYMBOL,
    LowHistoryUnavailable,
    low_history_status,
    pooled_low_history_forecast,
)
from services.forecast_execution import (
    ForecastExecutionOutcome,
    ForecastExecutionService,
    ForecastJobManager,
    ForecastJobNotFound,
    ForecastJobQueueFull,
    ForecastJobQuotaExceeded,
)
from services.forecast_presentation import DISCLAIMER as RESEARCH_DISCLAIMER
from services.forecast_presentation import BLOCKED_FORECAST_STATUSES, present_compare_item, present_forecast, public_report_payload
from services.model_registry import PUBLIC_MODEL_LABEL, registry_summary
from services.outcome_settlement import settle_due_forecasts
from services.auth_api import authenticate, create_access_token, list_sessions, register, revoke_all_sessions, revoke_session, revoke_token, user_from_token, validate_jwt_configuration
from authentication import login_or_register_oauth_user
from services.google_oauth import (
    GoogleOAuthError,
    build_authorization_url as build_google_authorization_url,
    exchange_code_for_profile as exchange_google_code,
    get_google_oauth_config,
)
from services.oauth_state import OAuthStateError, consume_transaction, issue_transaction
from services.compare_service import compare_symbols
from services.market_calendar import CalendarRefreshError, market_status, refresh_calendar
from services.market_data.base import (
    InstrumentNotFoundError,
    MarketDataError,
    TIMEFRAMES,
    UnsupportedHistoryRangeError,
    WINDOW_DAYS,
    WINDOWS,
)
from services.market_data.instruments import CATALOGUE
from services.market_data.reconcile_instruments import reconcile_instrument_master
from services.market_data.upstox import UpstoxProvider
from services.market_data.manager import MANAGER
from services.market_data.live_hub import LIVE_QUOTE_HUB
from services.market_data.derivatives_live import LIVE_DERIVATIVES, MarginRequest
from services.expected_move import expected_move_snapshot
from forecasting.drift_monitor import quality_dashboard as model_quality_dashboard
from services.paper_trading_v6 import badges as paper_badges
from services.paper_trading_v6 import cancel_order as cancel_paper_order
from services.paper_trading_v6 import ensure_account as ensure_paper_account
from services.paper_trading_v6 import journal as paper_journal
from services.paper_trading_v6 import leaderboard as paper_leaderboard
from services.paper_trading_v6 import place_order as place_paper_order
from services.paper_trading_v6 import process_open_orders
from services.paper_trading_v6 import weekly_challenges
from services.historical_replay import (
    InvalidChoiceError,
    ScenarioNotFoundError,
    list_scenarios as historical_replay_scenarios,
    my_attempts as historical_replay_attempts,
    record_attempt as historical_replay_record_attempt,
)
from services.paper_trading_v6 import set_leaderboard_opt_in
from services.reports import generate_forecast_pdf, generate_portfolio_pdf
from services.news import get_news

from utils.logging_config import configure_logging

__all__ = ['ADMIN_PRIVILEGES', 'API_METRICS', 'APP_VERSION', 'AccountLifecyclePayload', 'AdminConfigurationError', 'AdminSettingsPayload', 'AlertPayload', 'AlertTogglePayload', 'Any', 'BLOCKED_FORECAST_STATUSES', 'BacktestError', 'BannerDraftPayload', 'BannerPublishPayload', 'BaseModel', 'BriefUnavailable', 'CATALOGUE', 'CHART_LAYOUTS', 'CONFIDENCE_LEVELS', 'COOKIE_NAME', 'CORSMiddleware', 'CacheInvalidationPayload', 'CalendarRefreshError', 'CalibrationError', 'ChartLayoutError', 'ChartLayoutPayload', 'Cookie', 'Credentials', 'CsrfOriginMiddleware', 'DEFAULT_HORIZONS', 'DEFAULT_PEER_UNIVERSE', 'Depends', 'EQUITY_BACKTEST_FLAG', 'FORECAST_CONDITIONS', 'FORECAST_EXECUTION', 'FORECAST_JOBS', 'FORWARD_TESTS', 'FORWARD_TEST_FLAG', 'FastAPI', 'FeatureFlagPayload', 'Field', 'ForecastExecutionOutcome', 'ForecastExecutionService', 'ForecastJobManager', 'ForecastJobNotFound', 'ForecastJobPayload', 'ForecastJobQueueFull', 'ForecastJobQuotaExceeded', 'ForecastRequestContext', 'ForecastSettlementPayload', 'ForwardTestError', 'ForwardTestEvaluatePayload', 'ForwardTestStartPayload', 'FuturesPayload', 'GUARDRAILS', 'GoogleOAuthError', 'GuardrailError', 'HTTPException', 'Header', 'HistoricalChallengeChoicePayload', 'INVALIDATABLE_CACHES', 'InstrumentNotFoundError', 'InsufficientDataError', 'InvalidChoiceError', 'KillSwitchPayload', 'KillSwitchRevokePayload', 'LIVE_DERIVATIVES', 'LIVE_QUOTE_HUB', 'LOW_HISTORY_DISCLAIMER', 'LOW_HISTORY_FLAG', 'LeaderboardPrivacyPayload', 'Literal', 'LiveMarginPayload', 'LowHistoryUnavailable', 'MANAGER', 'MAX_ADMINS', 'MULTI_LEG_FLAG', 'MarginRequest', 'MarketDataError', 'MultiLegBacktestLegPayload', 'MultiLegBacktestPayload', 'OAUTH_STATE_COOKIE', 'OAuthStateError', 'OptionChainPayload', 'OptionChainRow', 'OptionGreeksPayload', 'OptionPayoffPayload', 'POOLED_MARKET_SYMBOL', 'PUBLIC_MODEL_LABEL', 'PaperOrderPayload', 'Path', 'PayoffError', 'PayoffLegPayload', 'PortfolioBuyPayload', 'PortfolioSellPayload', 'PortfolioUpdatePayload', 'PositionSizePayload', 'ProviderOrderPayload', 'Query', 'RESEARCH_DISCLAIMER', 'RateLimitMiddleware', 'RedirectResponse', 'Registration', 'Request', 'RequestContextMiddleware', 'ResearchAssistantPayload', 'Response', 'SAFE_MAINTENANCE_ACTIONS', 'SCREENS', 'STEP_UP', 'STEP_UP_ACTIONS', 'STEP_UP_MAINTENANCE_ACTIONS', 'STRATEGIES', 'STRATEGY_FLAG', 'SavedScreenPayload', 'ScenarioNotFoundError', 'ScreenerError', 'ScreenerFilterPayload', 'ScreenerRunPayload', 'SecurityHeadersMiddleware', 'StepUpChallengePayload', 'StepUpConfigurationError', 'StepUpError', 'StrategyBacktestPayload', 'StrategyConditionPayload', 'StrategyDefinitionPayload', 'StrategyError', 'StrategyGroupPayload', 'TIMEFRAMES', 'TRAINING_WINDOWS', 'UnsupportedForecastInstrument', 'UnsupportedHistoryRangeError', 'UpstoxProvider', 'WINDOWS', 'WINDOW_DAYS', 'WINDOW_TIMEFRAME_DEFAULTS', 'WatchlistPayload', 'WebSocket', 'WebSocketDisconnect', 'WorkspacePayload', '_EXPECTED_MOVE_CACHE', '_EXPECTED_MOVE_TTL_SECONDS', '_REFRESH_DIR', '_bearer_token', '_capture_sentiment', '_chart_layout_error', '_clear_oauth_state_cookie', '_compute_sector_rotation', '_execute_forecast', '_expected_move_for_symbol', '_feature_error', '_forecast_asset_class', '_forecast_diagnostics', '_forecast_engine_version', '_forecast_error', '_forecast_job_owner', '_frame_records', '_guard_forecast', '_history', '_history_loader', '_is_admin', '_job_error_payload', '_last_refreshed_map', '_log_sanitized', '_low_history_fallback', '_market_availability_error', '_oauth_failure_redirect', '_paper_account_snapshot', '_provenance', '_raise_market_error', '_require_feature', '_require_step_up', '_resolve_universe', '_run_forecast_job', '_secure_cookie', '_sentiment_history', '_serializable', '_set_oauth_state_cookie', '_set_session_cookie', '_strategy_body', '_support_id', '_truthy', '_watchlist_symbols', 'add_indicators', 'add_to_watchlist', 'admin_audit_events', 'aggregate_counts', 'analyze_futures_contract', 'analyze_option_chain', 'annotations', 'asynccontextmanager', 'asyncio', 'authenticate', 'backtest_multi_leg', 'backtest_strategy', 'binomial_greeks', 'binomial_tree', 'black_scholes', 'bootstrap_admins', 'build_google_authorization_url', 'build_morning_brief', 'build_payoff', 'buy_stock', 'calculate_performance_metrics', 'calculate_position_size', 'calibration_report', 'cancel_paper_order', 'compare_symbols', 'compile_strategy', 'configure_logging', 'configured_admin_emails', 'consume_transaction', 'create_access_token', 'create_price_alert', 'create_tables', 'current_user', 'database_health_check', 'datetime', 'delete_price_alert', 'delete_stock', 'deliver_triggered_alerts', 'describe_calibration_methodology', 'describe_fields', 'describe_metrics', 'describe_operators', 'describe_settings', 'detect_chart_patterns', 'enabled_features', 'ensure_paper_account', 'error_groups', 'error_summary', 'evaluate_price_alerts', 'exchange_google_code', 'expected_move_snapshot', 'expiry_bounded_scenario_interval', 'forecast_block_reason', 'forecast_range', 'generate_forecast_pdf', 'generate_portfolio_pdf', 'get_audit_events', 'get_connection', 'get_expiries', 'get_forecast_outcome_counts', 'get_google_oauth_config', 'get_news', 'get_portfolio', 'get_prediction_details', 'get_settings', 'get_settled_range_forecasts', 'get_settled_rows_for_quality', 'get_transactions', 'get_watchlist', 'group_by_horizon', 'group_interval_quality', 'hashlib', 'historical_replay_attempts', 'historical_replay_record_attempt', 'historical_replay_scenarios', 'history_support_matrix', 'implied_volatility', 'is_admin_email', 'issue_transaction', 'json', 'latest_model_health_by_group', 'list_price_alerts', 'list_sessions', 'logging', 'login_or_register_oauth_user', 'low_history_status', 'market_status', 'math', 'model_quality_dashboard', 'monte_carlo_projection', 'normalize_http_origin', 'np', 'optional_user', 'os', 'paper_badges', 'paper_journal', 'paper_leaderboard', 'parse_cors_origins', 'parse_qs', 'pd', 'place_paper_order', 'pooled_low_history_forecast', 'present_compare_item', 'present_forecast', 'price_returns', 'process_open_orders', 'public_report_payload', 'public_status_payload', 'reconcile_instrument_master', 'record_admin_action', 'record_error', 'refresh_calendar', 'register', 'registry_summary', 'remove_from_watchlist', 'require_admin', 'reset_error_groups', 'revoke_all_sessions', 'revoke_session', 'revoke_token', 'run_moving_average_strategy', 'run_rsi_strategy', 'run_screen', 'run_security_checks', 'run_setup_diagnostics', 'run_strategy', 'save_range_forecast', 'sell_stock', 'set_alert_active', 'set_leaderboard_opt_in', 'settle_due_forecasts', 'starter_strategies', 'time', 'timezone', 'update_settings', 'update_stock', 'urlencode', 'user_from_token', 'uuid', 'validate_jwt_configuration', 'weekly_challenges']

"""StockPilot AI v6 FastAPI backend.

This is the primary application backend for the Next.js terminal.  It keeps the
well-tested analytics/derivatives engines from v5, replaces Streamlit-bound
orchestration with REST/WebSocket contracts, uses the pluggable India-only
market-data layer, and exposes range forecasts instead of point-only prices.
"""


APP_VERSION = "18.0.0-rc.1"


COOKIE_NAME = "stockpilot_session"


OAUTH_STATE_COOKIE = "stockpilot_oauth_state"


FORECAST_EXECUTION = ForecastExecutionService()


FORECAST_JOBS = ForecastJobManager()


def _truthy(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Credentials(BaseModel):
    email: str
    password: str
    next: str = Field(default="/", max_length=500)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=80)
    #: Server-enforced age gate; absence is rejected by the auth service with a
    #: generic message, never a stack trace.
    date_of_birth: str | None = Field(default=None)


class AdminSettingsPayload(BaseModel):
    """A batch of allowlisted setting changes. Bounds are enforced server-side."""

    updates: dict[str, Any] = Field(default_factory=dict)


class WatchlistPayload(BaseModel):
    symbol: str


class HistoricalChallengeChoicePayload(BaseModel):
    choice: str


class LeaderboardPrivacyPayload(BaseModel):
    enabled: bool


class PortfolioBuyPayload(BaseModel):
    symbol: str
    company: str | None = None
    shares: float = Field(gt=0)
    buy_price: float = Field(gt=0)


class PortfolioUpdatePayload(BaseModel):
    shares: float = Field(gt=0)
    buy_price: float = Field(gt=0)


class PortfolioSellPayload(BaseModel):
    shares: float = Field(gt=0)
    sell_price: float = Field(gt=0)


class PositionSizePayload(BaseModel):
    account_value: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    risk_fraction: float = Field(default=0.01, gt=0, le=0.10)
    max_allocation: float = Field(default=0.25, gt=0, le=1.0)


class OptionGreeksPayload(BaseModel):
    spot: float = Field(gt=0)
    strike: float = Field(gt=0)
    days_to_expiry: int = Field(ge=1, le=3650)
    volatility: float = Field(gt=0, le=5)
    risk_free_rate: float = 0.065
    dividend_yield: float = 0.0
    option_type: Literal["call", "put"] = "call"
    market_price: float | None = Field(default=None, gt=0)
    underlying_type: Literal["index", "stock"] = "index"


class OptionChainRow(BaseModel):
    strike: float = Field(gt=0)
    option_type: str
    open_interest: float = Field(ge=0)


class OptionChainPayload(BaseModel):
    rows: list[OptionChainRow] = Field(min_length=2)
    spot_price: float | None = Field(default=None, gt=0)


class LiveMarginPayload(BaseModel):
    symbol: str
    quantity: int = Field(gt=0, le=1000000)
    transaction_type: Literal["BUY", "SELL"] = "BUY"
    product: Literal["D", "I"] = "D"
    instrument_type: Literal["EQUITY", "FUTURE", "OPTION"] = "FUTURE"
    price: float = Field(default=0.0, ge=0)
    lot_size: float = Field(default=1.0, gt=0)


class FuturesPayload(BaseModel):
    spot_price: float = Field(gt=0)
    futures_price: float = Field(gt=0)
    days_to_expiry: int = Field(ge=0, le=366)
    open_interest: float = Field(gt=0)
    previous_open_interest: float = Field(gt=0)
    previous_futures_price: float = Field(gt=0)
    annual_risk_free_rate: float = 0.065
    annual_carry_yield: float = 0.0
    realized_volatility: float | None = Field(default=None, ge=0, le=10)
    implied_volatility: float | None = Field(default=None, ge=0, le=10)


class PaperOrderPayload(BaseModel):
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0)
    order_type: Literal["MARKET", "LIMIT", "STOP", "TRAILING_STOP", "BRACKET"] = "MARKET"
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    trail_amount: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    spread_bps: float = Field(default=5.0, ge=0, le=500)
    slippage_bps: float = Field(default=2.0, ge=0, le=500)
    reasoning_notes: str | None = Field(default=None, max_length=1000)
    linked_alert_id: int | None = None
    instrument_type: Literal["EQUITY", "FUTURE", "OPTION"] = "EQUITY"
    expiry: str | None = None
    strike: float | None = Field(default=None, gt=0)
    option_type: Literal["CE", "PE"] | None = None
    lot_size: float = Field(default=1.0, gt=0)
    circuit_limit_pct: Literal[2, 5, 10, 20] = 20
    timeframe: Literal["1m", "5m", "15m", "1h", "4h", "1D", "1W"] = "1D"


class AlertPayload(BaseModel):
    symbol: str
    condition: Literal["ABOVE", "BELOW", "FORECAST_HIGH_ABOVE", "FORECAST_LOW_BELOW"]
    threshold: float = Field(gt=0)
    training_window: Literal["1w", "1mo", "3mo", "1y", "5y"] = "1mo"
    confidence_level: float = Field(default=0.80, ge=0.60, le=0.95)
    email_enabled: bool = False


class ProviderOrderPayload(BaseModel):
    order: list[str] = Field(min_length=1, max_length=12)


class AccountLifecyclePayload(BaseModel):
    model_config = {"str_strip_whitespace": True}
    account_id: int = Field(gt=0)
    action: Literal["suspend", "reinstate", "force_logout", "reset_lockout", "force_mfa_reset"]
    reason: str = Field(min_length=12, max_length=160)


class AlertTogglePayload(BaseModel):
    active: bool


class WorkspacePayload(BaseModel):
    layout: dict[str, Any] | str | None


class ForecastSettlementPayload(BaseModel):
    actual_price: float = Field(gt=0)


class ForecastJobPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    training_window: str = Field(default="1y", max_length=10)
    timeframe: str | None = Field(default=None, max_length=10)
    confidence: float = Field(default=0.80, gt=0.5, lt=0.99)


def _serializable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serializable(v) for v in value]
    if hasattr(value, "tolist"):
        return _serializable(value.tolist())
    return str(value)


def history_support_matrix() -> dict[str, dict[str, Any]]:
    """Report, per timeframe, the largest history the provider layer can serve.

    Minute and hour candles are capped by Upstox per-request date-span limits
    multiplied by the bounded number of segmented requests, so some
    timeframe/window pairs are permanently unavailable rather than briefly
    failing. Publishing the matrix lets the UI disable those pairs.
    """
    matrix: dict[str, dict[str, Any]] = {}
    for timeframe in sorted(TIMEFRAMES):
        supported_days = UpstoxProvider.max_supported_days(timeframe)
        matrix[timeframe] = {
            "max_days": supported_days,
            "windows": [name for name, days in sorted(WINDOW_DAYS.items(), key=lambda item: item[1]) if days <= supported_days],
        }
    return matrix


def _support_id() -> str:
    """Short opaque identifier tying a user-visible error to a server-side log."""
    return uuid.uuid4().hex[:12]


class UnsupportedForecastInstrument(ValueError):
    """The generic equity/index forecast is not valid for this asset class."""


def _log_sanitized(scope: str, support_id: str, exc: Exception) -> None:
    """Record the full exception server-side only, keyed by the support id.

    The response body never carries the exception text, so credentials, tokens
    and user content that a provider or driver may embed in a message cannot
    leak to the client.
    """
    fingerprint = record_error(scope, support_id, exc)
    reason = str(getattr(exc, "code", "") or "").strip() or "unknown"
    logging.getLogger("stockpilot.errors").error(
        "%s failed (support_id=%s fingerprint=%s exception_type=%s reason=%s)",
        scope,
        support_id,
        fingerprint,
        type(exc).__name__,
        reason,
    )


def _market_availability_error() -> tuple[str, str, int]:
    """Classify provider state without exposing provider responses."""
    health = MANAGER.health()
    providers = health.get("providers") or []
    configured_history = any(
        bool(item.get("configured"))
        and str(item.get("provider") or "").lower() in {"upstox"}
        for item in providers
        if isinstance(item, dict)
    )
    classifications = {
        str(item.get("classification") or "").lower()
        for item in MANAGER.failures.values()
        if isinstance(item, dict)
    }
    if not configured_history and health.get("provider_mode") == "LIVE_ONLY":
        return "provider_not_configured", "Configure and test a history-capable broker provider before requesting live research.", 503
    if classifications & {"expired", "revoked_or_invalid", "permission_denied"}:
        return "provider_auth_expired", "Broker authorization was rejected or expired. Reauthorize the configured provider.", 503
    if "rate_limited" in classifications:
        return "provider_rate_limited", "The provider rate limit was reached. Wait briefly before retrying.", 429
    return "history_unavailable", "Historical market data is unavailable. No substitute data is being shown.", 503


def _forecast_error(exc: Exception) -> HTTPException:
    """Translate a forecast failure into a sanitized, actionable HTTP error."""
    support_id = _support_id()
    _log_sanitized("forecast", support_id, exc)
    if isinstance(exc, UnsupportedHistoryRangeError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "history_range_unsupported",
                "message": "This timeframe cannot be combined with this training window.",
                "supported_windows": exc.supported_windows(),
                "support_id": support_id,
            },
        )
    if isinstance(exc, InsufficientDataError):
        return HTTPException(
            status_code=422,
            detail={
                "code": "insufficient_history",
                "message": "Not enough complete history for this instrument to build a research range. Try a longer training window.",
                "support_id": support_id,
            },
        )
    if isinstance(exc, (InstrumentNotFoundError, ValueError)):
        return HTTPException(
            status_code=422,
            detail={
                "code": "forecast_input_rejected",
                "message": "The requested instrument, timeframe or window cannot be used for a research range.",
                "support_id": support_id,
            },
        )
    if isinstance(exc, UnsupportedForecastInstrument):
        return HTTPException(
            status_code=422,
            detail={
                "code": "instrument_forecast_unsupported",
                "message": "This instrument class requires its separate derivatives scenario workflow; no generic equity range was substituted.",
                "support_id": support_id,
            },
        )
    if isinstance(exc, MarketDataError):
        code, message, status = _market_availability_error()
        return HTTPException(
            status_code=status,
            detail={"code": code, "message": message, "retryable": code != "provider_not_configured", "support_id": support_id},
        )
    return HTTPException(
        status_code=503,
        detail={
            "code": "forecast_unavailable",
            "message": "The research range could not be produced right now. Retry shortly.",
            "retryable": True,
            "support_id": support_id,
        },
    )


def _raise_market_error(exc: Exception) -> None:
    support_id = _support_id()
    _log_sanitized("market_data", support_id, exc)
    if isinstance(exc, UnsupportedHistoryRangeError):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "history_range_unsupported",
                "message": "This timeframe cannot serve that much history. Choose a shorter window.",
                "supported_windows": exc.supported_windows(),
                "support_id": support_id,
            },
        ) from exc
    if isinstance(exc, InstrumentNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "instrument_not_found",
                "message": "That symbol could not be resolved on NSE or BSE.",
                "support_id": support_id,
            },
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "market_request_invalid", "message": "Invalid market-data request.", "support_id": support_id},
        ) from exc
    if isinstance(exc, MarketDataError):
        code, message, status = _market_availability_error()
        raise HTTPException(
            status_code=status,
            detail={"code": code, "message": message, "retryable": code != "provider_not_configured", "support_id": support_id},
        ) from exc
    raise HTTPException(
        status_code=503,
        detail={
            "code": "market_data_unavailable",
            "message": "Live market data is temporarily unavailable. No substitute data is shown.",
            "retryable": True,
            "support_id": support_id,
        },
    ) from exc


def _bearer_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def current_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = _bearer_token(authorization) or session
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user = user_from_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists.")
    return user


def optional_user(
    session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any] | None:
    """Resolve the caller when a valid session exists, otherwise return ``None``.

    Used by endpoints that are public but reveal more to an administrator. A bad
    or expired token is treated exactly like no token, so a public endpoint never
    fails because of a stale cookie.
    """
    token = _bearer_token(authorization) or session
    if not token:
        return None
    try:
        return user_from_token(token) or None
    except ValueError:
        return None


def _forecast_job_owner(request: Request, user: dict[str, Any] | None) -> int:
    """Use the account ID when signed in and a stable, opaque per-IP bucket otherwise."""
    if user is not None:
        return int(user["id"])
    host = request.client.host if request.client is not None else "anonymous"
    digest = hashlib.sha256(f"stockpilot-forecast-job:{host}".encode("utf-8")).digest()
    return -(int.from_bytes(digest[:4], "big") + 1)


def _is_admin(user: dict[str, Any] | None) -> bool:
    """True only for an account whose stored role *and* configured email agree.

    Requiring both means a stale ``admin`` row left in the database by an earlier
    configuration cannot keep privilege after the allowlist changes.
    """
    if not user:
        return False
    if str(user.get("role") or "user").strip().lower() != "admin":
        return False
    return is_admin_email(str(user.get("email") or ""))


def require_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Authorize one of the configured administrators, or refuse with 403."""
    if not _is_admin(user):
        record_admin_action(
            actor_id=user.get("id"),
            actor_email=user.get("email"),
            action="admin_access_denied",
            outcome="denied",
        )
        raise HTTPException(
            status_code=403,
            detail={"code": "admin_required", "message": "This area is restricted to administrators."},
        )
    return user


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme.lower() == "https" or _truthy("STOCKPILOT_COOKIE_SECURE")


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_secure_cookie(request),
        samesite="lax",
        max_age=int(os.getenv("STOCKPILOT_SESSION_HOURS", "12")) * 3600,
        path="/",
    )


def _set_oauth_state_cookie(response: Response, request: Request, state: str, *, provider: str) -> None:
    secure = _secure_cookie(request)
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=600,
        path="/api/v1/auth/",
    )


def _clear_oauth_state_cookie(response: Response, request: Request, *, provider: str) -> None:
    secure = _secure_cookie(request)
    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/api/v1/auth/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


def _oauth_failure_redirect(request: Request, provider: str, code: str, exc: Exception | None = None) -> RedirectResponse:
    support_id = _support_id()
    if exc is not None:
        _log_sanitized(f"oauth_{provider}", support_id, exc)
    response = RedirectResponse(
        url=f"/login?{urlencode({'oauth_error': code, 'support_id': support_id})}",
        status_code=303,
    )
    _clear_oauth_state_cookie(response, request, provider=provider)
    return response


def _provenance(frame: pd.DataFrame) -> dict[str, Any]:
    """Where the numbers came from, in terms a normal user can act on.

    Provider identity, staleness and market state are user-facing facts about the
    data, not model internals, so they are safe to publish and are required so a
    range is never read as fresher than it is.
    """
    status = market_status()
    as_of = frame.index[-1] if len(frame.index) else None
    raw_context = frame.attrs.get("context")
    context: dict[str, Any] = raw_context if isinstance(raw_context, dict) else {}
    source = str(frame.attrs.get("source", frame.attrs.get("provider", "unknown")))
    is_stale = bool(frame.attrs.get("is_stale", context.get("is_stale", False)))
    is_live = bool(context["is_live"]) if "is_live" in context else source.lower() not in {"demo", "demo_india", "yfinance"}
    return {
        "source": source,
        "as_of": as_of,
        "is_stale": is_stale,
        "is_live": is_live and not is_stale,
        "market_state": status.get("state") or status.get("status"),
        "bars_used": len(frame),
    }


def _history(symbol: str, timeframe: str, window: str) -> tuple[str, pd.DataFrame]:
    try:
        normalized = MANAGER.normalize_symbol(symbol)
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe. Choose one of {sorted(TIMEFRAMES)}")
        if window not in WINDOWS:
            raise ValueError(f"Unsupported window. Choose one of {sorted(WINDOWS)}")
        with trace_market_data_operation("get_history", normalized, timeframe=timeframe, window=window):
            frame = MANAGER.get_history(normalized, timeframe=timeframe, window=window)
        return normalized, frame
    except Exception as exc:
        _raise_market_error(exc)
        raise AssertionError("unreachable")


def _forecast_engine_version(
    *,
    horizons: tuple[int, ...] | None = None,
    explain: bool = False,
) -> str:
    """Separate cached results when the forecast implementation or its options change."""
    base = f"{forecast_range.__module__}:{forecast_range.__qualname__}:{id(forecast_range)}"
    options = [f"h={','.join(map(str, horizons or (1,)))}", f"explain={int(bool(explain))}"]
    return f"{base}|{'|'.join(options)}"


_EXPECTED_MOVE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


_EXPECTED_MOVE_TTL_SECONDS = 300.0


def _expected_move_for_symbol(symbol: str) -> dict[str, Any]:
    """Best-effort ATM-implied expected-move snapshot with a short TTL.

    The forecast must never fail because an option chain is unreachable, so this
    helper converts any provider error into an honest ``available=False`` block.
    """
    key = str(symbol or "").strip().upper()
    if not key:
        return {"underlying": key, "available": False, "reason": "underlying_required"}
    now_mono = time.monotonic()
    cached = _EXPECTED_MOVE_CACHE.get(key)
    if cached and now_mono - cached[0] < _EXPECTED_MOVE_TTL_SECONDS:
        return cached[1]
    try:
        snapshot = expected_move_snapshot(
            key,
            chain_provider=lambda underlying, expiry: LIVE_DERIVATIVES.live_chain(underlying, expiry),
            expiry_provider=lambda underlying: LIVE_DERIVATIVES.expiries(underlying),
        )
    except Exception:
        snapshot = {
            "underlying": key,
            "available": False,
            "reason": "chain_unavailable",
            "message": "The options-implied expected move is temporarily unavailable.",
        }
    _EXPECTED_MOVE_CACHE[key] = (now_mono, snapshot)
    return snapshot


def _forecast_asset_class(instrument_type: str) -> str:
    """Map the instrument master's type onto a kill-switch asset class."""
    key = str(instrument_type or "").strip().upper()
    if key == "INDEX":
        return "index"
    if key in {"FUT", "FUTURE", "FUTURES", "FUTIDX", "FUTSTK"}:
        return "futures"
    if key in {"OPT", "OPTION", "OPTIONS", "OPTIDX", "OPTSTK", "CE", "PE"}:
        return "options"
    return "equity"


def _fetch_index_history(index_symbol: str, timeframe: str, window: str) -> pd.DataFrame | None:
    """Fetch index history (NIFTY 50, NIFTY BANK) via the market data manager.

    Returns None on failure so the caller can degrade gracefully.
    """
    try:
        return MANAGER.get_history(index_symbol, timeframe=timeframe, window=window)
    except Exception:
        return None


def _fetch_vix_history(timeframe: str, window: str) -> pd.DataFrame | None:
    """Fetch India VIX history via the market data manager.

    India VIX is not a tradable instrument in the standard catalogue, so we
    attempt to fetch it by its common symbol. Returns None on failure so the
    caller can use the realized-volatility proxy.
    """
    try:
        # Try common VIX symbols; the manager will resolve via provider fallback.
        return MANAGER.get_history("INDIA VIX", timeframe=timeframe, window=window)
    except Exception:
        return None


def _get_fno_membership(symbol: str) -> bool:
    """Check if the symbol has active F&O contracts in the instrument master."""
    try:
        instrument = CATALOGUE.resolve(symbol)
        contracts = [
            item
            for item in CATALOGUE.load()
            if item.segment in {"NSE_FO", "BSE_FO"} and (item.underlying_symbol or "").upper() == symbol.upper()
        ]
        return bool(contracts)
    except Exception:
        return False


def _ipo_info_for_symbol(symbol: str) -> dict[str, Any] | None:
    """Return IPO metadata for the symbol if available.

    Currently a stub returning None; replace with a real IPO calendar lookup
    when a licensed feed is configured.
    """
    return None


def _peer_history_loader(symbol: str, timeframe: str, window: str) -> pd.DataFrame:
    """Load peer history on demand for IPO peer transfer.

    This is a best-effort loader that returns an empty DataFrame on failure.
    The v14 integration layer treats missing history as an unavailable input
    rather than an error.
    """
    try:
        return MANAGER.get_history(symbol, timeframe=timeframe, window=window)
    except Exception:
        return pd.DataFrame()


def _execute_forecast(
    symbol: str,
    timeframe: str,
    training_window: str,
    confidence: float,
    *,
    horizons: tuple[int, ...] | None = None,
    explain: bool = False,
) -> ForecastExecutionOutcome:
    normalized = MANAGER.normalize_symbol(symbol)
    instrument = CATALOGUE.resolve(normalized)
    instrument_type = str(instrument.instrument_type if instrument else "").upper()
    # Equities, indices and futures flow through the same per-symbol range
    # pipeline. Futures are contract-master aware: the trading symbol (e.g.
    # "BANKNIFTY FUT 27 OCT 26") resolves through the NSE_FO catalogue exactly
    # like an equity, so expiry, lot size and instrument key come from the same
    # single source of truth. The kill-switch asset class for futures is
    # distinct from equity, so an operator can still suppress them separately.
    if instrument_type not in {"EQ", "EQUITY", "INDEX", "FUT", "FUTIDX", "FUTSTK"}:
        raise UnsupportedForecastInstrument(f"Generic forecast unsupported for instrument type {instrument_type or 'unknown'}.")
    _guard_forecast(normalized, timeframe, instrument_type)
    # The promotion receipt is the gate's shipping verdict: without a fresh,
    # passed manifest the CQR challenger stays out of the published bounds.
    promotion_receipt = active_promotion_receipt()

    # --- Live context wiring for v14 enhancements ---
    # Fetch market index data (NIFTY 50 for sector context)
    market_data = _fetch_index_history("NIFTY 50", timeframe, training_window)
    # Fetch India VIX data (with realized-vol proxy fallback inside forecast_range)
    vix_data = _fetch_vix_history(timeframe, training_window)
    # NOTE: F&O membership is intentionally NOT computed here. forecast_range
    # -> collect_context already resolves it independently from the same
    # instrument catalogue (see forecasting/v14_integration.py:collect_context,
    # "fno_membership" ContextInput) and forecast_range has no `is_fno`
    # parameter to receive one anyway. `_get_fno_membership` used to be called
    # here and its result silently discarded -- a harmless but confusing dead
    # computation, removed rather than left as misleading dead code. The
    # helper function itself is left in place in case a future caller needs a
    # standalone F&O check outside the forecast path.
    # IPO metadata (stub until licensed feed available)
    ipo_info = _ipo_info_for_symbol(normalized)
    # Peer history loader for IPO peer transfer
    history_loader = _peer_history_loader
    # Prefer metadata-matched peers. Contract masters can omit research
    # metadata, so retain a documented generic fallback rather than silently
    # disabling peer transfer for those symbols.
    peer_universe = select_ipo_peers(normalized, CATALOGUE.load(), n=10)
    if not peer_universe:
        peer_universe = [peer for peer in DEFAULT_PEER_UNIVERSE if peer != normalized][:10]

    with trace_forecast_operation("range_forecast", normalized, timeframe=timeframe, window=training_window, confidence=confidence):
        return FORECAST_EXECUTION.execute(
            symbol=normalized,
            timeframe=timeframe,
            window=training_window,
            confidence=confidence,
            history_loader=lambda: _history(normalized, timeframe, training_window),
            forecaster=lambda normalized, frame: forecast_range(
                normalized,
                frame,
                confidence_level=confidence,
                training_window=training_window,
                timeframe=timeframe,
                horizons=horizons,
                explain=explain,
                market_data=market_data,
                vix_data=vix_data,
                context_history_loader=history_loader,
                cqr_promotion_receipt=promotion_receipt,
                ipo_info=ipo_info,
                peer_universe=peer_universe,
            ),
            engine_version=_forecast_engine_version(horizons=horizons, explain=explain),
        )


def _guard_forecast(symbol: str, timeframe: str, instrument_type: str) -> None:
    """Refuse a forecast while an operator kill switch covers this request.

    Checked before any history load or model work, so a suppressed request can
    never produce, cache, or publish an interval.
    """
    decision = forecast_block_reason(
        ForecastRequestContext(
            symbol=symbol,
            asset_class=_forecast_asset_class(instrument_type),
            timeframe=timeframe,
            model_version=PUBLIC_MODEL_LABEL,
        )
    )
    if not decision.get("blocked"):
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": decision.get("code", "forecast_disabled_by_operator"),
            "message": decision.get("reason") or "Forecasts are paused by an operator.",
            "scope": decision.get("scope"),
            "target": decision.get("target"),
            "expires_at": decision.get("expires_at"),
            "retryable": True,
        },
    )


def _job_error_payload(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        if isinstance(exc.detail, dict):
            return _serializable(exc.detail)
        return {"code": "forecast_request_failed", "message": "The forecast request could not be completed."}
    translated = _forecast_error(exc if isinstance(exc, Exception) else RuntimeError("Forecast worker stopped."))
    return _serializable(translated.detail)


def _run_forecast_job(request: dict[str, Any], *, is_admin: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    outcome = _execute_forecast(
        request["symbol"],
        request["timeframe"],
        request["training_window"],
        float(request["confidence"]),
        horizons=DEFAULT_HORIZONS,
        explain=is_admin,
    )
    payload = present_forecast(outcome.result, is_admin=is_admin, provenance=_provenance(outcome.frame), expected_move=_expected_move_for_symbol(request["symbol"]))
    payload["context"] = outcome.frame.attrs.get("context")
    return _serializable(payload), outcome.trace


def _frame_records(frame: pd.DataFrame, limit: int = 5000) -> list[dict[str, Any]]:
    view = frame.tail(max(1, min(int(limit), 100_000)))
    rows: list[dict[str, Any]] = []
    for index, row in view.iterrows():
        rows.append({
            "time": pd.Timestamp(str(index)).isoformat(),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row["Volume"]),
        })
    return rows


def _paper_account_snapshot(user_id: int) -> dict[str, Any]:
    account = ensure_paper_account(user_id)
    conn = get_connection()
    rows = conn.execute("SELECT symbol,quantity,average_price,updated_at,lot_size FROM paper_positions WHERE user_id=? ORDER BY symbol", (int(user_id),)).fetchall()
    orders = conn.execute(
        "SELECT id,symbol,side,quantity,price,notional,status,created_at,order_type,limit_price,stop_price,trail_amount,reasoning_notes,instrument_type,expiry,strike,option_type,margin_required,realized_pnl,target_price,parent_order_id,oco_group,lot_size FROM paper_orders WHERE user_id=? ORDER BY id DESC LIMIT 100",
        (int(user_id),),
    ).fetchall()
    conn.close()
    positions = []
    market_value = 0.0
    for symbol, qty, avg, updated, lot_size in rows:
        base = str(symbol).split(":", 1)[0]
        try:
            with trace_provider_call("upstox", "get_quote", base):
                quote = MANAGER.get_quote(base).to_dict()
            mark = float(quote["price"])
            context = quote.get("context")
        except Exception:
            mark = float(avg)
            context = None
        multiplier = max(1.0, float(lot_size or 1.0))
        value = float(qty) * mark * multiplier
        market_value += value
        positions.append({"symbol": symbol, "quantity": float(qty), "average_price": float(avg), "mark_price": mark, "market_value": value, "unrealized_pnl": (mark-float(avg))*float(qty)*multiplier, "lot_size": multiplier, "updated_at": updated, "context": context})
    order_keys = ["id","symbol","side","quantity","price","notional","status","created_at","order_type","limit_price","stop_price","trail_amount","reasoning_notes","instrument_type","expiry","strike","option_type","margin_required","realized_pnl","target_price","parent_order_id","oco_group","lot_size"]
    return {
        **account,
        "positions": positions,
        "orders": [dict(zip(order_keys, row)) for row in orders],
        "market_value": round(market_value, 2),
        "equity": round(float(account["cash_balance"]) + market_value, 2),
        "badges": paper_badges(user_id),
    }


_REFRESH_DIR = Path(__file__).resolve().parents[1] / "cache" / "model_refresh"


def _last_refreshed_map() -> dict[str, dict[str, Any]]:
    """ISO timestamps of the scheduled model-refresh artifacts per symbol.

    Both scheduled refreshes and drift auto-retrains write JSON artifacts into
    ``cache/model_refresh``; their file mtimes are the most direct, storage-free
    evidence of when a symbol's model was last (re)trained.
    """
    if not _REFRESH_DIR.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(_REFRESH_DIR.glob("*.json")):
        stem = path.stem
        symbol = stem.replace("auto_retrain_", "").replace("_", " ").strip()
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            continue
        trigger = "drift_auto_retrain" if stem.startswith("auto_retrain_") else "scheduled_refresh"
        result[symbol] = {"last_refreshed": mtime, "trigger": trigger, "artifact": path.name}
    return result


class ScreenerFilterPayload(BaseModel):
    field: str
    op: str
    value: float | None = None
    low: float | None = None
    high: float | None = None


class ScreenerRunPayload(BaseModel):
    filters: list[ScreenerFilterPayload] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    universe: str = "watchlist"
    sort_by: str | None = None
    descending: bool = True
    limit: int = 50
    timeframe: str = "1D"
    window: str = "1y"


class SavedScreenPayload(ScreenerRunPayload):
    name: str


class PayoffLegPayload(BaseModel):
    type: str
    side: str = "buy"
    strike: float | None = None
    premium: float = 0.0
    quantity: float = 1.0
    lot_size: float = 1.0
    label: str | None = None


class OptionPayoffPayload(BaseModel):
    legs: list[PayoffLegPayload] = Field(default_factory=list)
    spot: float
    underlying: str | None = None
    grid_span: float = 0.20
    grid_points: int = 121
    volatility: float | None = None
    days_to_expiry: int | None = None
    risk_free_rate: float = 0.065
    dividend_yield: float = 0.0
    costs: float = 0.0


def _feature_error(exc: Exception, *, status_code: int = 422) -> HTTPException:
    """Convert a service-level error into the standard API error envelope."""
    return HTTPException(
        status_code=status_code,
        detail={
            "code": getattr(exc, "code", "invalid_request"),
            "message": getattr(exc, "message", str(exc)),
            "retryable": False,
        },
    )


def _history_loader(timeframe: str, window: str):
    """History loader for the research surfaces, bound to one timeframe/window."""

    def loader(symbol: str) -> pd.DataFrame:
        _, frame = _history(symbol, timeframe, window)
        return frame

    return loader


def _watchlist_symbols(user: dict[str, Any]) -> list[str]:
    return [str(row[1]).strip().upper() for row in get_watchlist(user["id"]) if str(row[1] or "").strip()]


def _resolve_universe(symbols: list[str], universe: str, user: dict[str, Any]) -> tuple[list[str], str]:
    explicit = [str(item).strip().upper() for item in symbols if str(item or "").strip()]
    if explicit:
        return explicit, "request"
    if str(universe or "watchlist").strip().lower() == "watchlist":
        return _watchlist_symbols(user), "watchlist"
    return [], "none"


class StrategyConditionPayload(BaseModel):
    metric: str
    operator: str
    value: float | None = None
    values: list[float] | None = None
    compare_metric: str | None = None


class StrategyGroupPayload(BaseModel):
    join: str = "and"
    conditions: list[StrategyConditionPayload] = Field(default_factory=list)


class StrategyDefinitionPayload(BaseModel):
    name: str
    symbols: list[str] = Field(default_factory=list)
    entry: list[StrategyGroupPayload] = Field(default_factory=list)
    exit: list[StrategyGroupPayload] | None = None
    quantity: float = 1.0
    stop_loss_pct: float | None = None
    target_pct: float | None = None
    timeframe: str = "1D"
    window: str = "1y"


class StrategyBacktestPayload(StrategyDefinitionPayload):
    symbol: str | None = None
    spread_bps: float = 5.0
    slippage_bps: float = 2.0


class MultiLegBacktestLegPayload(BaseModel):
    type: str
    side: str = "buy"
    strike_offset_pct: float
    contracts: float = 1.0
    lot_size: float = 1.0


class MultiLegBacktestPayload(BaseModel):
    underlying: str
    legs: list[MultiLegBacktestLegPayload] = Field(default_factory=list)
    days_to_expiry: int = 30
    entry_every_sessions: int | None = None
    volatility: float = 0.20
    risk_free_rate: float = 0.065
    dividend_yield: float = 0.0
    costs_per_cycle: float = 0.0
    timeframe: str = "1D"
    window: str = "2y"


class ForwardTestStartPayload(BaseModel):
    strategy_id: int
    name: str | None = None
    symbols: list[str] = Field(default_factory=list)


class ForwardTestEvaluatePayload(BaseModel):
    timeframe: str = "1D"
    window: str = "1y"


def _require_feature(flag: str) -> None:
    """Refuse a flagged capability instead of half-serving it."""
    if not GUARDRAILS.is_feature_enabled(flag):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "feature_disabled",
                "message": "This capability is currently turned off by an operator feature flag.",
                "retryable": False,
            },
        )


def _strategy_body(payload: StrategyDefinitionPayload) -> dict[str, Any]:
    def groups(items: list[StrategyGroupPayload] | None) -> list[dict[str, Any]]:
        return [
            {
                "join": str(group.join or "and").lower(),
                "conditions": [
                    {key: value for key, value in condition.model_dump().items() if value is not None}
                    for condition in group.conditions
                ]
            }
            for group in (items or [])
        ]

    body: dict[str, Any] = {
        "name": payload.name,
        "symbols": payload.symbols,
        "entry": groups(payload.entry),
        "quantity": payload.quantity,
    }
    if payload.exit is not None:
        body["exit"] = groups(payload.exit)
    if payload.stop_loss_pct is not None:
        body["stop_loss_pct"] = payload.stop_loss_pct
    if payload.target_pct is not None:
        body["target_pct"] = payload.target_pct
    return body


class ChartLayoutPayload(BaseModel):
    name: str
    symbol: str = Field(default="", min_length=1, max_length=60)
    timeframe: str = "1D"
    overlays: dict[str, bool] = Field(default_factory=dict)
    visible_range: dict[str, float] | None = None


def _chart_layout_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=404 if getattr(exc, "code", "") == "chart_layout_not_found" else 422,
        detail={
            "code": getattr(exc, "code", "invalid_request"),
            "message": getattr(exc, "message", str(exc)),
            "retryable": False,
        },
    )


def _sentiment_history(symbol: str, *, days: int = 30) -> dict[str, Any]:
    """Delegate to the shipped snapshot service (same module the scheduler uses)."""
    from services.sentiment_snapshot import history as _snapshot_history

    return _snapshot_history(symbol, days=days)


def _capture_sentiment(symbol: str) -> dict[str, Any]:
    """Fold the symbol's latest headlines into one stored [-1,+1] snapshot."""
    from services.sentiment_snapshot import capture_snapshot as _snapshot_capture

    return _snapshot_capture(symbol)


class ResearchAssistantPayload(BaseModel):
    symbol: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=3, max_length=2000)


ADMIN_PRIVILEGES = {
    "may": [
        "View application health, provider status and dependency availability",
        "View the model registry, calibration summaries and sanitized error groups",
        "Change allowlisted settings within fixed server-side bounds",
        "Trigger safe maintenance actions",
        "View aggregate counts and the administrator audit log",
    ],
    "may_not": [
        "Read or change any individual user's portfolio, watchlist, orders, alerts, reports, forecasts or journal",
        "Read passwords, password hashes, OAuth tokens, broker credentials or any secret",
        "Impersonate a user, reset another account or place any broker order",
        "Execute commands, run SQL, browse the filesystem or dump the environment",
    ],
}


SAFE_MAINTENANCE_ACTIONS = {"refresh_instruments", "refresh_calendar", "clear_error_groups", "bootstrap_admins", "settle_forecast_outcomes"}


STEP_UP_MAINTENANCE_ACTIONS = {"bootstrap_admins"}


INVALIDATABLE_CACHES = ("forecast_cache", "forecast_jobs", "all")


LOW_HISTORY_DISCLAIMER = (
    "Research range only, not investment advice. This instrument has too little history for the "
    "per-symbol model, so the range comes from a model trained across many instruments."
)


class StepUpChallengePayload(BaseModel):
    password: str
    action: str
    target: str | None = None


class KillSwitchPayload(BaseModel):
    scope: str
    reason: str
    target: str | None = None
    expires_in_hours: float = 6.0


class KillSwitchRevokePayload(BaseModel):
    reason: str


class FeatureFlagPayload(BaseModel):
    enabled: bool
    reason: str
    rollout_percent: int = 0


class BannerDraftPayload(BaseModel):
    level: str
    headline: str
    body: str
    starts_at: str | None = None
    ends_in_hours: float = 6.0


class BannerPublishPayload(BaseModel):
    confirmed_preview: bool = False


class CacheInvalidationPayload(BaseModel):
    cache: str
    reason: str


def _require_step_up(admin: dict[str, Any], action: str, target: str | None, token: str | None) -> Any:
    """Consume a single-use step-up token before a sensitive operator action.

    An authenticated admin session is never sufficient on its own: the operator
    must re-enter their password to mint a short-lived, single-use token that is
    bound to this exact action and target.
    """
    email = str(admin.get("email") or "")
    if not token:
        record_admin_action(
            actor_id=admin.get("id"), actor_email=email, action=f"step_up:{action}",
            target=target, outcome="rejected", detail={"reason": "missing_token"},
        )
        raise HTTPException(
            status_code=401,
            detail={
                "code": "step_up_required",
                "message": "Confirm your password to authorise this action.",
                "action": action,
                "target": target,
                "token_ttl_seconds": STEP_UP.requirements()["token_ttl_seconds"],
            },
        )
    try:
        return STEP_UP.consume(
            actor_email=email, token=token, action=action,
            target=target, actor_id=admin.get("id"),
        )
    except StepUpConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "step_up_unavailable", "message": str(exc), "retryable": True},
        ) from exc
    except StepUpError as exc:
        raise HTTPException(
            status_code=401,
            detail={"code": "step_up_required", "message": str(exc), "action": action, "target": target},
        ) from exc


def _low_history_fallback(
    symbol: str,
    timeframe: str,
    training_window: str,
    confidence: float,
    user: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Pooled cross-instrument range for instruments with too little history.

    Returns ``None`` whenever the pooled path is disabled, unavailable, or
    cannot be validated, so the caller keeps the documented
    ``insufficient_history`` refusal instead of inventing a number.
    """
    try:
        normalized = MANAGER.normalize_symbol(symbol)
    except Exception:
        return None
    subject = str((user or {}).get("email") or normalized)

    def loader(candidate: str):
        return MANAGER.get_history(candidate, timeframe, training_window)

    try:
        payload = pooled_low_history_forecast(
            normalized,
            timeframe=timeframe,
            training_window=training_window,
            confidence=confidence,
            history_loader=loader,
            peer_symbols=DEFAULT_PEER_UNIVERSE,
            market_symbol=POOLED_MARKET_SYMBOL,
            subject=subject,
        )
    except LowHistoryUnavailable:
        return None
    except Exception as exc:  # a fallback must never become a 500
        support_id = _support_id()
        _log_sanitized("low_history_forecast", support_id, exc)
        return None
    payload["disclaimer"] = LOW_HISTORY_DISCLAIMER
    payload["feature_flag"] = LOW_HISTORY_FLAG
    return payload


def _forecast_diagnostics() -> dict[str, Any]:
    return _serializable({
        "jobs": FORECAST_JOBS.diagnostics(),
        "cache": {
            "forecast_results": FORECAST_EXECUTION.diagnostics(),
            "market_history": MANAGER.cache_diagnostics(),
        },
        "providers": MANAGER.provider_readiness(),
    })
