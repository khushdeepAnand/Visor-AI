"""Router for the system domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403
from services.compliance import RESEARCH_ACKNOWLEDGMENT_VERSION, RESEARCH_ONLY_DISCLAIMER


router = APIRouter()



@router.get("/api/v1/system")
def system_endpoint() -> dict[str, Any]:
    market = market_status()
    database = database_health_check()
    providers = MANAGER.provider_readiness()
    if providers.get("status") == "not_started":
        MANAGER.start_readiness_probe()
        providers = MANAGER.provider_readiness()
    provider_health = MANAGER.health()
    return _serializable({
        "version": APP_VERSION,
        "market": {
            key: market.get(key)
            for key in ("exchange", "timezone", "regular_session", "is_open", "reason", "timestamp")
        },
        "database_status": str(database.get("status", "Unavailable")).lower(),
        "instrument_count": len(CATALOGUE.load()),
        "instrument_master": CATALOGUE.health(),
        "providers": {
            **providers,
            "mode": MANAGER.provider_mode.value,
            "order": list(MANAGER.order),
            "metrics": provider_health["metrics"],
        },
        "range_forecasting": {
            "training_windows": sorted(TRAINING_WINDOWS),
            "timeframes": sorted(TIMEFRAMES),
            "supported_combinations": history_support_matrix(),
        },
        # The public system view names the forecasting approach only by its
        # concise label. Model classes, weights, diagnostics and dependency
        # status are administrator-only and live under /api/v1/admin/models.
        "forecast_engine": PUBLIC_MODEL_LABEL,
        "disclaimer": RESEARCH_DISCLAIMER,
        "research_acknowledgment": {
            "version": RESEARCH_ACKNOWLEDGMENT_VERSION,
            "text": RESEARCH_ONLY_DISCLAIMER,
        },
    })


@router.get("/api/v1/status")
def public_status_endpoint() -> dict[str, Any]:
    """Unauthenticated status surface consumed by the global banner."""
    payload = public_status_payload()
    payload["version"] = APP_VERSION
    return _serializable(payload)


@router.get("/api/v1/features")
def feature_flags_endpoint(user: dict[str, Any] | None = Depends(optional_user)) -> dict[str, Any]:
    """Feature-flag map for the calling subject (staged rollout is subject-stable)."""
    subject = str(user.get("email")) if user else None
    return _serializable({"features": enabled_features(subject=subject)})


@router.post("/api/v1/system/providers/test", status_code=202)
def user_provider_test_endpoint(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Run the same bounded, secret-free readiness checks available at startup."""
    del user
    MANAGER.start_readiness_probe()
    return {"accepted": True, "providers": MANAGER.provider_readiness()}
