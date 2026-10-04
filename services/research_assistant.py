"""Grounded-only conversational research assistant over one symbol (K5).

The assistant is allowed to state figures only if they come from facts the
application has actually ingested for that symbol: stored trading history,
persisted forecast snapshots, and stored sentiment snapshots. Every figure in
an answer must be traced back to one of those facts; a provider response that
introduces a number outside the corpus is refused entirely rather than passed
through. Advice-shaped questions (buy/sell/invest/positioning) are refused
before any provider is called, and the response permanently carries the
disclosure language from ``REGULATORY_REVIEW_REQUIRED.md``.

The provider is an OpenAI-compatible ``/chat/completions`` endpoint configured
by ``STOCKPILOT_LLM_BASE_URL``, ``STOCKPILOT_LLM_API_KEY`` and
``STOCKPILOT_LLM_MODEL``. With no provider configured the assistant still
returns the fully sourced fact table plus an explicit refusal, so the shipped
offline-demo build never fabricates.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse

import requests
from datetime import datetime
from typing import Any, Callable

import pandas as pd

SCHEMA_VERSION = "research-assistant-v1"
RESEARCH_FLAG = "research_assistant"

PROVIDER_BASE_URL_ENV = "STOCKPILOT_LLM_BASE_URL"
PROVIDER_API_KEY_ENV = "STOCKPILOT_LLM_API_KEY"
PROVIDER_MODEL_ENV = "STOCKPILOT_LLM_MODEL"
REQUEST_TIMEOUT_SECONDS = 25.0

MIN_SESSIONS = 30

DISCLOSURE = (
    "The assistant answers only from this application's ingested history for the symbol you selected. "
    "It is not personalized investment advice, a buy/sell call, an earnings opinion, or a guaranteed target. "
    "Papers and forward tests are simulated and were not achieved in any live account. "
    "Any figure it cannot attribute to that ingested history is refused by design."
)

#: Deterministic refusal for advice-shaped questions, checked before any
#: provider call so a configured model never fields a recommendation request.
ADVICE_PATTERN = re.compile(
    r"\b(should i|buy|sell|invest|accumulate|exit (my|your) position|long (a|the|this)? position"
    r"|short (a|the|this)? position|worth buying|worth selling|target price( to)? (buy|sell))\b",
    re.IGNORECASE,
)

NUMERIC_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
YEAR_PATTERN = re.compile(r"^[12]\d{3}$")

LLM_PROVIDER = Callable[[list[dict[str, str]]], str]
HistoryLoader = Callable[[str], pd.DataFrame]
ForecastLoader = Callable[[str], dict[str, Any] | None]
SentimentLoader = Callable[[str], dict[str, Any] | None]


class ResearchAssistantError(Exception):
    """Domain error surfaced by the API as a sanitized 422 envelope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GroundedFact:
    """One figure, formatted exactly as the model is allowed to state it."""

    __slots__ = ("label", "value", "source", "as_of")

    def __init__(self, label: str, value: str, source: str, as_of: str) -> None:
        self.label = label
        self.value = value
        self.source = source
        self.as_of = as_of

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "value": self.value, "source": self.source, "as_of": self.as_of}


def _fmt(value: Any) -> str:
    """Format a number the way the model is allowed to reproduce it."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number != number or number in (float("inf"), float("-inf")):
        return ""
    if number.is_integer():
        return f"{number:.0f}"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _pct(value: Any) -> str:
    return f"{_fmt(value)}%"


def _tokens(text: str) -> set[str]:
    """Normalized numeric tokens (commas removed) so 1,234.5 == 1234.5."""
    found: set[str] = set()
    for match in NUMERIC_PATTERN.finditer(text or ""):
        found.add(match.group(0).replace(",", ""))
    return found


def _is_benign_number(token: str) -> bool:
    """Only calendar years are allowed to appear outside the corpus."""
    return bool(YEAR_PATTERN.match(token))


def _validate_numbers(text: str, allowed: set[str]) -> bool:
    """True when every numeric token in ``text`` is an allowed/grounded number."""
    for token in _tokens(text):
        if token in allowed or _is_benign_number(token):
            continue
        return False
    return True


def _provider_configured() -> bool:
    return bool(
        os.getenv(PROVIDER_BASE_URL_ENV, "").strip()
        and os.getenv(PROVIDER_MODEL_ENV, "").strip()
    )


def _call_provider(messages: list[dict[str, str]], client: LLM_PROVIDER | None) -> str:
    if client is not None:
        return client(messages)
    base_url = os.getenv(PROVIDER_BASE_URL_ENV, "").strip().rstrip("/")
    api_key = os.getenv(PROVIDER_API_KEY_ENV, "").strip()
    model = os.getenv(PROVIDER_MODEL_ENV, "").strip()
    if not base_url or not model:
        raise ResearchAssistantError(
            "provider_not_configured",
            "No OpenAI-compatible research model is configured, so this question is not answered.",
        )
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 500,
        }
    ).encode("utf-8")
    endpoint = f"{base_url}/chat/completions"
    scheme = urllib.parse.urlsplit(endpoint).scheme.lower()
    if scheme not in {"http", "https"}:
        raise ResearchAssistantError(
            "provider_url_invalid",
            "The configured research-provider URL must use HTTP or HTTPS.",
        )
    try:
        response = requests.post(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = json.loads(response.content.decode("utf-8"))
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (401, 403):
            raise ResearchAssistantError(
                "provider_auth_failed",
                "The configured research-provider credentials were rejected.",
            ) from exc
        raise ResearchAssistantError(
            "provider_request_failed",
            "The research provider returned an error response. Retry shortly.",
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise ResearchAssistantError(
            "provider_unreachable",
            "The research provider could not be reached. Retry shortly.",
        ) from exc
    choices = (payload or {}).get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content:
        raise ResearchAssistantError(
            "provider_empty_response",
            "The research provider returned no answer to this question.",
        )
    return str(content).strip()


def _system_prompt(symbol: str, facts: list[GroundedFact]) -> str:
    rows = "\n".join(f"- {fact.label}: {fact.value} (source: {fact.source}, as_of: {fact.as_of})" for fact in facts)
    return (
        f"You are a grounding-only research assistant for {symbol}. "
        "You may only state figures that appear verbatim in the facts below; every number you "
        "mention must be copied exactly from the Value column. Never invent prices, percentages, "
        "counts, dates, or targets. If the question asks for anything these facts do not cover, "
        "say the ingested history does not support that answer. Never give personalized investment "
        "advice, buy/sell calls, or guaranteed targets.\n\n"
        f"Facts for {symbol}:\n{rows}"
    )


def build_corpus(
    *,
    symbol: str,
    history_loader: HistoryLoader,
    forecast_loader: ForecastLoader | None = None,
    sentiment_loader: SentimentLoader | None = None,
) -> list[GroundedFact]:
    """Assemble the sourced fact table for one symbol from ingested data only."""
    frame = history_loader(symbol)
    if frame is None or frame.empty or len(frame) < MIN_SESSIONS:
        raise ResearchAssistantError(
            "insufficient_history",
            f"The stored history for {symbol} does not support an answer yet (requires at least {MIN_SESSIONS} trading sessions).",
        )
    closes = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    highs = pd.to_numeric(frame["High"], errors="coerce").dropna() if "High" in frame else closes
    lows = pd.to_numeric(frame["Low"], errors="coerce").dropna() if "Low" in frame else closes
    if len(closes) < MIN_SESSIONS:
        raise ResearchAssistantError(
            "insufficient_history",
            f"The stored history for {symbol} does not support an answer yet (requires at least {MIN_SESSIONS} trading sessions).",
        )

    last_row = frame.tail(1).iloc[0]
    as_of = str(last_row.name if hasattr(last_row.name, "isoformat") else last_row.name)
    facts: list[GroundedFact] = []

    def add(label: str, value: str, source: str) -> None:
        facts.append(GroundedFact(label, value, source, as_of))

    add("last close", _fmt(closes.iloc[-1]), f"stored daily close for {symbol}")
    add("sessions in stored history", f"{len(closes)}", f"stored daily history for {symbol}")
    add("highest high, last 20 sessions", _fmt(highs.tail(20).max()), f"stored history for {symbol}")
    add("lowest low, last 20 sessions", _fmt(lows.tail(20).min()), f"stored history for {symbol}")

    if len(closes) >= 6:
        change_5d = (closes.iloc[-1] / closes.iloc[-6] - 1.0) * 100.0
        add("change over last 5 sessions", _pct(change_5d), "computed from stored daily closes")
    if len(closes) >= 21:
        change_20d = (closes.iloc[-1] / closes.iloc[-21] - 1.0) * 100.0
        add("change over last 20 sessions", _pct(change_20d), "computed from stored daily closes")

    latest_raw = forecast_loader(symbol) if forecast_loader is not None else None
    forecast_details: dict[str, Any] | None = latest_raw if isinstance(latest_raw, dict) else None
    if forecast_details and forecast_details.get("forecast_median") is not None:
        payload_maybe = forecast_details.get("payload")
        payload: dict[str, Any] = payload_maybe if isinstance(payload_maybe, dict) else {}
        model_label = str(
            forecast_details.get("model_label")
            or payload.get("model_label")
            or forecast_details.get("best_model")
            or "forecast model"
        )
        status = str(forecast_details.get("forecast_status") or "available")
        as_of_forecast = str(forecast_details.get("target_timestamp") or forecast_details.get("prediction_date") or "")
        forecast_source = f"latest stored forecast for {symbol} ({model_label}, {status})"
        facts.append(GroundedFact("forecast median", _fmt(forecast_details["forecast_median"]), forecast_source, as_of_forecast))
        if forecast_details.get("forecast_low") is not None:
            facts.append(GroundedFact("forecast low", _fmt(forecast_details["forecast_low"]), forecast_source, as_of_forecast))
        if forecast_details.get("forecast_high") is not None:
            facts.append(GroundedFact("forecast high", _fmt(forecast_details["forecast_high"]), forecast_source, as_of_forecast))

    sentiment_raw = sentiment_loader(symbol) if sentiment_loader is not None else None
    sentiment_details: dict[str, Any] | None = sentiment_raw if isinstance(sentiment_raw, dict) else None
    if sentiment_details and sentiment_details.get("label"):
        as_of_sentiment = str(sentiment_details.get("snapshot_at") or "")
        sentiment_source = f"latest stored sentiment snapshot for {symbol}"
        facts.append(GroundedFact("latest stored sentiment", str(sentiment_details["label"]), sentiment_source, as_of_sentiment))
        if sentiment_details.get("count") is not None:
            facts.append(GroundedFact("sentiment snapshots stored", f"{int(sentiment_details['count'])}", sentiment_source, as_of_sentiment))

    return facts


def research_answer(
    *,
    symbol: str,
    question: str,
    history_loader: HistoryLoader,
    forecast_loader: ForecastLoader | None = None,
    sentiment_loader: SentimentLoader | None = None,
    client: LLM_PROVIDER | None = None,
) -> dict[str, Any]:
    """Answer one question, grounded strictly in the ingested fact table.

    Returns a ``{"refused": bool, ...}`` envelope. A refusal never contains an
    invented figure; it carries the sourced fact table so the user still sees
    what the application actually knows about the symbol.
    """
    question = str(question or "").strip()
    if len(question) < 3:
        raise ResearchAssistantError("question_too_short", "Type a question of at least a few words.")

    facts = build_corpus(
        symbol=symbol,
        history_loader=history_loader,
        forecast_loader=forecast_loader,
        sentiment_loader=sentiment_loader,
    )
    fact_dicts = [fact.to_dict() for fact in facts]

    def refusal(code: str, message: str, answer: str | None = None) -> dict[str, Any]:
        return {
            "question": question,
            "symbol": symbol,
            "configured": _provider_configured(),
            "mode": "refused",
            "refused": True,
            "refusal_code": code,
            "refusal_message": message,
            "answer": answer or "",
            "facts": fact_dicts,
            "disclosure": DISCLOSURE,
            "schema_version": SCHEMA_VERSION,
        }

    if ADVICE_PATTERN.search(question):
        return refusal(
            "advice_unsupported",
            "This assistant does not give investment advice or buy/sell suggestions.",
        )

    available = _provider_configured() or client is not None
    if not available:
        return refusal(
            "provider_not_configured",
            "No OpenAI-compatible research model is configured for this deployment. The ingested facts below are the verified answer the application can currently give.",
        )

    messages = [
        {"role": "system", "content": _system_prompt(symbol, facts)},
        {"role": "user", "content": question},
    ]
    try:
        raw = _call_provider(messages, client=client)
    except ResearchAssistantError as exc:
        return refusal(exc.code, exc.message, answer="The ingested fact table below is the only verified information available.")

    allowed: set[str] = set()
    for fact in facts:
        allowed.update(_tokens(fact.value))
        allowed.update(_tokens(fact.label))
        allowed.update(_tokens(fact.source))
        allowed.update(_tokens(fact.as_of))

    if not _validate_numbers(raw, allowed):
        return refusal(
            "figure_ungrounded",
            "The model proposed a figure that is not in this application's ingested history, so the answer was refused.",
        )
    if ADVICE_PATTERN.search(raw):
        return refusal(
            "advice_unsupported",
            "The model drifted into advice phrasing, so the answer was refused.",
        )

    return {
        "question": question,
        "symbol": symbol,
        "configured": _provider_configured(),
        "mode": "answer",
        "refused": False,
        "refusal_code": None,
        "refusal_message": None,
        "answer": raw,
        "facts": fact_dicts,
        "disclosure": DISCLOSURE,
        "schema_version": SCHEMA_VERSION,
    }


def provider_status() -> dict[str, Any]:
    """Admin-facing configuration status. Never returns the key."""
    return {
        "configured": _provider_configured(),
        "base_url": str(os.getenv(PROVIDER_BASE_URL_ENV, "")).strip() or None,
        "model": str(os.getenv(PROVIDER_MODEL_ENV, "")).strip() or None,
        "api_key_set": bool(os.getenv(PROVIDER_API_KEY_ENV, "").strip()),
        "schema_version": SCHEMA_VERSION,
    }
