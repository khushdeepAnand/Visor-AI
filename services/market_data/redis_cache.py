"""Optional Redis hot cache for market-data fan-out.

StockPilot runs without Redis. When ``STOCKPILOT_REDIS_ENABLED=true`` and the
``redis`` package/server are available, quote/history cache entries are shared
between API processes. This keeps provider requests coalesced in multi-worker
deployments while preserving the credential-free local path.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from .base import Quote

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def history_to_json(frame: pd.DataFrame) -> str:
    """Serialize history without an executable object format."""
    payload = {
        "schema": 1,
        "frame": json.loads(frame.to_json(orient="split", date_format="iso")),
        "attrs": frame.attrs,
    }
    return json.dumps(payload, default=str, separators=(",", ":"))


def history_from_json(raw: str | bytes) -> pd.DataFrame:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise ValueError("Unsupported history-cache schema")
    split = payload.get("frame")
    attrs = payload.get("attrs")
    if not isinstance(split, dict) or not isinstance(attrs, dict):
        raise ValueError("Invalid history-cache payload")
    columns, index, data = split.get("columns"), split.get("index"), split.get("data")
    if not isinstance(columns, list) or not isinstance(index, list) or not isinstance(data, list) or len(index) != len(data):
        raise ValueError("Invalid history-cache frame")
    frame = pd.DataFrame(data, columns=[str(column) for column in columns])
    frame.index = pd.to_datetime(index, errors="raise")
    frame.attrs.update(attrs)
    return frame


class RedisHotCache:
    def __init__(self) -> None:
        self.enabled = os.getenv("STOCKPILOT_REDIS_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        self.url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self._client: Any = None
        self.error: str | None = None
        if self.enabled:
            try:
                import redis
                client = redis.Redis.from_url(self.url, socket_connect_timeout=0.5, socket_timeout=0.8, decode_responses=False)
                client.ping()
                self._client = client
            except Exception as exc:  # optional infrastructure boundary
                self.error = str(exc)
                self._client = None

    @property
    def active(self) -> bool:
        return self._client is not None

    def get_quote(self, key: str) -> Quote | None:
        if not self._client: return None
        try:
            raw = self._client.get(f"sp:q:{key}")
            if not raw: return None
            return Quote(**json.loads(raw.decode("utf-8")))
        except Exception as exc:
            self.error = str(exc); return None

    def set_quote(self, key: str, quote: Quote, ttl: int) -> None:
        if not self._client: return
        try: self._client.setex(f"sp:q:{key}", max(1, int(ttl)), json.dumps(quote.to_dict(), default=str))
        except Exception as exc: self.error = str(exc)

    def get_history(self, key: str) -> pd.DataFrame | None:
        if not self._client: return None
        try:
            raw = self._client.get(f"sp:h:{key}")
            value = history_from_json(raw) if raw else None
            return value if isinstance(value, pd.DataFrame) else None
        except Exception as exc:
            self.error = str(exc); return None

    def set_history(self, key: str, frame: pd.DataFrame, ttl: int) -> None:
        if not self._client: return
        try: self._client.setex(f"sp:h:{key}", max(1, int(ttl)), history_to_json(frame))
        except Exception as exc: self.error = str(exc)

    def health(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "active": self.active, "error": self.error}


REDIS_HOT_CACHE = RedisHotCache()
