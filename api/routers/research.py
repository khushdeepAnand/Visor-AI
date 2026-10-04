"""Router for the research domain (extracted from api/main.py)."""

from fastapi import APIRouter
from api.deps import *  # noqa: F401,F403


router = APIRouter()



@router.get("/api/v1/audit")
def audit_endpoint(limit: int = Query(100, ge=1, le=1000), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"items": get_audit_events(user["id"], limit)}


@router.get("/api/v1/sector-rotation")
def sector_rotation_endpoint() -> dict[str, Any]:
    _require_feature("sector_rotation")
    return _serializable(_compute_sector_rotation())


@router.get("/api/v1/sentiment/{symbol}")
def sentiment_trend_endpoint(
    symbol: str,
    days: int = Query(30, ge=1, le=365),
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_feature("sentiment_trend")
    return _serializable(_sentiment_history(symbol, days=days))


@router.post("/api/v1/sentiment/{symbol}/capture")
def sentiment_capture_endpoint(
    symbol: str,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    _require_feature("sentiment_trend")
    return _serializable(_capture_sentiment(symbol))


@router.post("/api/v1/research/assistant")
def research_assistant_endpoint(
    payload: ResearchAssistantPayload,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Grounding-only conversational research assistant over one symbol (K5)."""
    from services.research_assistant import ResearchAssistantError, research_answer

    _require_feature("research_assistant")
    symbol = str(payload.symbol).strip().upper()

    def _latest_forecast_for(_symbol: str) -> dict[str, Any] | None:
        records = get_prediction_details(user["id"], symbol=_symbol, limit=1)
        return records[0] if records else None

    def _sentiment_for(_symbol: str) -> dict[str, Any] | None:
        snapshot = _sentiment_history(_symbol, days=30)
        series = (snapshot or {}).get("series") or []
        if not series:
            return None
        latest = series[-1]
        return {
            "snapshot_at": latest["snapshot_at"],
            "label": latest["label"],
            "score": latest["avg_score"],
            "count": snapshot.get("snapshot_count", len(series)),
        }

    try:
        result = research_answer(
            symbol=symbol,
            question=str(payload.question).strip(),
            history_loader=_history_loader("1D", "1y"),
            forecast_loader=_latest_forecast_for,
            sentiment_loader=_sentiment_for,
        )
    except ResearchAssistantError as exc:
        raise _feature_error(exc) from exc
    return _serializable(result)
