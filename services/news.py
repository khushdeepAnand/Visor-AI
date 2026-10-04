"""Bounded, attribution-preserving RSS news connector for Indian instruments."""

from __future__ import annotations

import hashlib
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from defusedxml import ElementTree as SafeET

from services.market_data.manager import MANAGER
from services.sentiment import score_headline

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TAG = re.compile(r"<[^>]+>")


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(_TAG.sub(" ", unescape(str(value or ""))).split())[:limit]


def _safe_url(value: object) -> str | None:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else None


def get_news(symbol: str, limit: int = 10) -> dict[str, Any]:
    symbol = MANAGER.normalize_symbol(symbol)
    limit = max(1, min(int(limit), 25))
    template = os.getenv("STOCKPILOT_NEWS_RSS_URL", "").strip()
    publisher = _clean(os.getenv("STOCKPILOT_NEWS_PUBLISHER", "Configured RSS source"), 100)
    if not template:
        return {"symbol": symbol, "status": "unavailable", "source": None, "is_stale": False, "items": [], "reason": "news_source_not_configured"}
    key = f"{symbol}:{limit}"
    ttl = max(60, int(os.getenv("STOCKPILOT_NEWS_CACHE_SECONDS", "300")))
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] <= ttl:
        return {**cached[1], "cached": True}
    url = template.replace("{symbol}", quote(symbol, safe=""))
    if _safe_url(url) is None:
        return {"symbol": symbol, "status": "unavailable", "source": publisher, "is_stale": False, "items": [], "reason": "news_source_invalid"}
    try:
        response = requests.get(url, timeout=8, headers={"Accept": "application/rss+xml, application/atom+xml, text/xml", "User-Agent": "StockPilotAI/7"})
        response.raise_for_status()
        payload = response.content
        if len(payload) > 2_000_000:
            raise ValueError("News response exceeded the safe size limit.")
        root = SafeET.fromstring(payload)
        rows = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            title = _clean(row.findtext("title") or row.findtext("{http://www.w3.org/2005/Atom}title"), 300)
            link_node = row.find("link") or row.find("{http://www.w3.org/2005/Atom}link")
            link = _safe_url((link_node.text if link_node is not None else None) or (link_node.get("href") if link_node is not None else None))
            if not title or not link:
                continue
            identity = hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()[:20]
            if identity in seen:
                continue
            seen.add(identity)
            published = _clean(row.findtext("pubDate") or row.findtext("published") or row.findtext("{http://www.w3.org/2005/Atom}updated"), 80) or None
            sentiment = score_headline(title)
            sentiment.pop("fallback_reason", None)
            items.append({"id": identity, "title": title, "url": link, "publisher": publisher, "published_at": published, "sentiment": sentiment})
            if len(items) >= limit:
                break
        result = {"symbol": symbol, "status": "available", "source": publisher, "fetched_at": datetime.now(timezone.utc).isoformat(), "is_stale": False, "items": items}
        _CACHE[key] = (time.time(), result)
        return result
    except Exception:
        if cached:
            return {**cached[1], "status": "stale", "is_stale": True, "cached": True}
        return {"symbol": symbol, "status": "unavailable", "source": publisher, "is_stale": False, "items": [], "reason": "news_source_unavailable"}
