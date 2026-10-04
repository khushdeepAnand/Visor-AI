"""Framework-neutral market-data freshness helpers."""
from __future__ import annotations
from datetime import timezone
from typing import Any
import pandas as pd


def get_data_freshness(data: pd.DataFrame | None) -> dict[str, Any]:
    if not isinstance(data, pd.DataFrame):
        return {"updated_at": None, "is_stale": False, "warning": "", "source": "Unknown"}
    fetched_at = data.attrs.get("fetched_at")
    timestamp = (
        pd.NaT
        if fetched_at is None
        else pd.to_datetime(str(fetched_at), utc=True, errors="coerce")
    )
    if pd.isna(timestamp) and not data.empty:
        timestamp = pd.to_datetime(data.index[-1], utc=True, errors="coerce")
    updated_at = None if pd.isna(timestamp) else timestamp.to_pydatetime()
    return {
        "updated_at": updated_at,
        "is_stale": bool(data.attrs.get("is_stale", False)),
        "warning": str(data.attrs.get("fetch_warning", "") or ""),
        "source": str(data.attrs.get("source") or data.attrs.get("provider") or "Unknown"),
    }


def format_data_freshness(data: pd.DataFrame | None, label: str = "Data") -> str:
    metadata = get_data_freshness(data); timestamp = metadata["updated_at"]
    if timestamp is None: return f"{label} update time unavailable"
    if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    suffix = " · stale fallback" if metadata["is_stale"] else ""
    return f"{label} last updated: {timestamp:%d %b %Y, %H:%M UTC}{suffix} · Source: {metadata['source']}"
