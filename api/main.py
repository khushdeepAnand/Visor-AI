"""StockPilot AI v6 FastAPI backend (app assembly).

Domain routers live under api/routers/ and share infrastructure via
api/deps.py. api.main re-exports everything deps defines so the import
surface visible to tests and services is unchanged.
"""
from __future__ import annotations

from api.deps import *  # noqa: F401,F403
from api.deps import (  # noqa: F401
    APP_VERSION, COOKIE_NAME, OAUTH_STATE_COOKIE,
    StrategyDefinitionPayload, StrategyGroupPayload,
    _strategy_body, _forecast_asset_class, _feature_error,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    validate_jwt_configuration()
    create_tables()
    try:
        # Idempotent: promotes only accounts that are both listed in local
        # configuration and already registered, and demotes every other admin
        # row so a removed entry cannot keep privilege across a restart.
        bootstrap_admins()
    except AdminConfigurationError:
        # A malformed or oversized allowlist must not grant privilege and must
        # not stop the application from serving normal users.
        logging.getLogger("stockpilot.admin").error("Administrator allowlist is invalid; no account was promoted.")
    scheduler = None
    retention_scheduler = None
    FORECAST_JOBS.start()
    if os.getenv("STOCKPILOT_ENV", "").strip().lower() != "test":
        MANAGER.start_readiness_probe()
        if _truthy("STOCKPILOT_RETENTION_ENABLED", "true"):
            try:
                from api.scheduler import create_retention_scheduler
                retention_scheduler = create_retention_scheduler()
                retention_scheduler.start()
            except Exception:
                logging.getLogger("stockpilot.retention").exception("Retention scheduler failed to start")
    if _truthy("STOCKPILOT_REFRESH_INSTRUMENTS_ON_START"):
        try:
            await asyncio.to_thread(CATALOGUE.refresh_from_upstox)
        except Exception:
            # The bundled NSE master keeps the app usable offline.
            pass
    if _truthy("STOCKPILOT_ENABLE_SCHEDULER"):
        try:
            from api.scheduler import create_scheduler
            scheduler = create_scheduler()
            scheduler.start()
        except Exception:
            scheduler = None
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        if retention_scheduler is not None:
            retention_scheduler.shutdown(wait=False)
        await LIVE_QUOTE_HUB.shutdown()
        FORECAST_JOBS.shutdown()
        from services.telemetry import shutdown_telemetry
        shutdown_telemetry()


app = FastAPI(
    title="StockPilot AI API",
    description="India-only market terminal API with interval forecasts and paper trading.",
    version=APP_VERSION,
    lifespan=lifespan,
)


allowed_origins = parse_cors_origins(
    os.getenv("STOCKPILOT_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
)


app.add_middleware(
    CsrfOriginMiddleware,
    cookie_name=COOKIE_NAME,
    allowed_origins=allowed_origins,
)


app.add_middleware(RateLimitMiddleware)


app.add_middleware(RequestContextMiddleware)


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(SecurityHeadersMiddleware)


@app.get("/")
def root() -> dict[str, Any]:
    return {"service": "StockPilot AI API", "version": APP_VERSION, "docs": "/docs", "status": "operational"}


@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    database = database_health_check()
    market_health = MANAGER.health()
    return {
        "status": "operational" if database.get("status") == "Operational" else "degraded",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": {"database": str(database.get("status", "Unavailable")).lower()},
        "market_data": {
            "provider_mode": market_health["provider_mode"],
            "providers": market_health["providers"],
        },
        "version": APP_VERSION,
    }


@app.get("/api/v1/ready")
def readiness() -> dict[str, Any]:
    database = database_health_check()
    if database.get("status") != "Operational":
        raise HTTPException(status_code=503, detail="Database is not ready.")
    return {"status": "ready", "version": APP_VERSION}


@app.get("/api/v1/metrics")
def api_metrics() -> dict[str, Any]:
    snapshot = API_METRICS.snapshot()
    return {
        "service": "StockPilot AI API",
        "version": APP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "request_count": snapshot["request_count"],
        "error_count": snapshot["error_count"],
        "error_rate": snapshot["error_rate"],
        "average_latency_ms": snapshot["average_latency_ms"],
    }


@app.get("/metrics", response_class=Response)
def prometheus_metrics() -> Response:
    """Aggregate process metrics for a trusted-network Prometheus scraper."""
    return Response(API_METRICS.prometheus(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.websocket("/ws/quotes/{symbol}")
async def quote_stream(websocket: WebSocket, symbol: str, interval_seconds: float = 1.0, timeframe: str = "1m"):
    """Fan out one provider polling stream per symbol to every browser subscriber."""
    origin = websocket.headers.get("origin")
    authorization = websocket.headers.get("authorization")
    bearer = _bearer_token(authorization)
    cookie_token = websocket.cookies.get(COOKIE_NAME)
    try:
        normalized_origin = normalize_http_origin(origin) if origin else None
    except ValueError:
        normalized_origin = None
    # Anonymous non-browser clients remain supported. Browsers presenting an
    # Origin, and every cookie-authenticated upgrade, must be same-site approved.
    if (origin and normalized_origin not in allowed_origins) or (cookie_token and not bearer and normalized_origin not in allowed_origins):
        await websocket.close(code=1008, reason="Origin is not allowed.")
        return
    token = bearer or cookie_token
    if token:
        try:
            if not user_from_token(token):
                raise ValueError("Account no longer exists.")
        except (ValueError, RuntimeError):
            await websocket.close(code=1008, reason="Authentication failed.")
            return
    del interval_seconds  # hub cadence is centrally controlled to coalesce provider calls
    if timeframe not in TIMEFRAMES:
        await websocket.close(code=1008, reason="Unsupported timeframe.")
        return
    try:
        normalized = MANAGER.normalize_symbol(symbol)
    except ValueError:
        await websocket.close(code=1008, reason="Unsupported symbol.")
        return
    await websocket.accept()
    queue = await LIVE_QUOTE_HUB.subscribe(normalized)
    try:
        while True:
            payload = await queue.get()
            payload = dict(payload)
            if isinstance(payload.get("context"), dict):
                payload["context"] = dict(payload["context"], timeframe=timeframe)
            payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
            await websocket.send_json(_serializable(payload))
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        await LIVE_QUOTE_HUB.unsubscribe(normalized, queue)


@app.websocket("/ws/price/{symbol}")
async def legacy_price_stream(websocket: WebSocket, symbol: str, interval_seconds: float = 1.0):
    """Compatibility alias for v5 clients during cutover."""
    await quote_stream(websocket, symbol, interval_seconds)


from api.routers.admin import router as admin_router  # noqa: F401
from api.routers.admin_operations import router as admin_operations_router  # noqa: F401
from api.routers.analytics import router as analytics_router  # noqa: F401
from api.routers.auth import router as auth_router  # noqa: F401
from api.routers.derivatives import router as derivatives_router  # noqa: F401
from api.routers.forecasting import router as forecasting_router  # noqa: F401
from api.routers.layouts import router as layouts_router  # noqa: F401
from api.routers.market import router as market_router  # noqa: F401
from api.routers.research import router as research_router  # noqa: F401
from api.routers.screener import router as screener_router  # noqa: F401
from api.routers.strategies import router as strategies_router  # noqa: F401
from api.routers.strategy_lab import router as strategy_lab_router  # noqa: F401
from api.routers.system import router as system_router  # noqa: F401
from api.routers.trading import router as trading_router  # noqa: F401
from api.routers.webauthn import router as webauthn_router  # noqa: F401

app.include_router(admin_router)
app.include_router(admin_operations_router)
app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(derivatives_router)
app.include_router(forecasting_router)
app.include_router(layouts_router)
app.include_router(market_router)
app.include_router(research_router)
app.include_router(screener_router)
app.include_router(strategies_router)
app.include_router(strategy_lab_router)
app.include_router(system_router)
app.include_router(trading_router)
app.include_router(webauthn_router)

# OpenTelemetry request spans: no-op unless STOCKPILOT_OTEL_ENABLED (or an
# OTLP endpoint) is configured, so tests and local runs stay unaffected.
from services.telemetry import setup_telemetry  # noqa: E402

setup_telemetry(app)

