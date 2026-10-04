"""K5 research assistant: strict grounding, advice refusal, provider gating.

Focus is the service contract in ``services/research_assistant.py``: every
figure a provider answer may contain must exist verbatim in the ingested fact
table, advice-shaped questions are refused before any provider call, and an
unconfigured deployment returns only the sourced facts plus an explicit
refusal.
"""

import json
import os
from typing import Any, Callable

import pandas as pd
import pytest

from services.research_assistant import (
    PROVIDER_BASE_URL_ENV,
    PROVIDER_MODEL_ENV,
    RESEARCH_FLAG,
    ResearchAssistantError,
    build_corpus,
    provider_status,
    research_answer,
)

from services.forecast_guardrails import FEATURE_FLAGS


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROVIDER_BASE_URL_ENV, raising=False)
    monkeypatch.delenv("STOCKPILOT_LLM_API_KEY", raising=False)
    monkeypatch.delenv(PROVIDER_MODEL_ENV, raising=False)


def _frame(sessions: int = 120, *, last_close: float = 140.0) -> pd.DataFrame:
    import numpy as np

    dates = pd.bdate_range("2024-01-01", periods=sessions)
    closes = np.linspace(100.0, last_close, sessions)
    return pd.DataFrame(
        {
            "Open": closes - 0.5,
            "High": closes + 1.0,
            "Low": closes - 1.0,
            "Close": closes,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


def _history_loader(frame: pd.DataFrame) -> Callable[[str], pd.DataFrame]:
    def loader(symbol: str) -> pd.DataFrame:
        assert symbol == "RELIANCE"
        return frame

    return loader


def _forecast_loader(symbol: str) -> None:
    return None


def _sentiment_loader(symbol: str) -> None:
    return None


def _last_close(frame: pd.DataFrame) -> str:
    value = float(frame["Close"].iloc[-1])
    return f"{value:.0f}" if value.is_integer() else f"{value:.4f}".rstrip("0").rstrip(".")


def test_flag_known_and_enabled() -> None:
    assert RESEARCH_FLAG in FEATURE_FLAGS
    assert FEATURE_FLAGS[RESEARCH_FLAG]["default"] is True


def test_unconfigured_returns_sourced_facts_and_refusal(_clear_provider_env: None) -> None:
    frame = _frame(last_close=140.0)
    result = research_answer(
        symbol="RELIANCE",
        question="What is happening with the price?",
        history_loader=_history_loader(frame),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
    )
    assert result["configured"] is False
    assert result["refused"] is True
    assert result["refusal_code"] == "provider_not_configured"
    assert result["answer"] == ""
    assert result["mode"] == "refused"
    assert result["disclosure"]
    labels = {fact["label"] for fact in result["facts"]}
    assert "last close" in labels
    assert "sessions in stored history" in labels
    assert result["facts"][0]["value"] == "140"


def test_advice_question_refused_before_provider() -> None:
    calls: list[list[dict[str, str]]] = []

    def client(messages: list[dict[str, str]]) -> str:
        calls.append(messages)
        return "The last close was 140."

    result = research_answer(
        symbol="RELIANCE",
        question="Should I buy RELIANCE right now?",
        history_loader=_history_loader(_frame()),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
        client=client,
    )
    assert result["refused"] is True
    assert result["refusal_code"] == "advice_unsupported"
    assert calls == []


def test_grounded_answer_passes_numeric_verification() -> None:
    frame = _frame(last_close=140.0)
    grounded_close = _last_close(frame)

    def client(messages: list[dict[str, str]]) -> str:
        assert "RELIANCE" in str(messages[0]["content"])
        return f"The last close for RELIANCE was {grounded_close} in the stored history. The stored history has sessions in the fact table."

    result = research_answer(
        symbol="RELIANCE",
        question="What is the last close?",
        history_loader=_history_loader(frame),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
        client=client,
    )
    assert result["refused"] is False
    assert result["mode"] == "answer"
    assert result["refusal_code"] is None
    assert grounded_close in result["answer"]


def test_invented_figure_refused_and_never_returned() -> None:
    def client(messages: list[dict[str, str]]) -> str:
        return "The last close was 199.99 tomorrow."

    result = research_answer(
        symbol="RELIANCE",
        question="What is the last close?",
        history_loader=_history_loader(_frame()),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
        client=client,
    )
    assert result["refused"] is True
    assert result["refusal_code"] == "figure_ungrounded"
    assert result["answer"] == ""
    assert "199.99" not in json.dumps(result["answer"])


def test_advice_phrasing_in_provider_answer_refused() -> None:
    def client(messages: list[dict[str, str]]) -> str:
        return "You should buy RELIANCE; the last close was 140."

    result = research_answer(
        symbol="RELIANCE",
        question="What is the last close?",
        history_loader=_history_loader(_frame()),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
        client=client,
    )
    assert result["refused"] is True
    assert result["refusal_code"] == "advice_unsupported"
    assert result["answer"] == ""


def test_insufficient_history_raises() -> None:
    with pytest.raises(ResearchAssistantError) as error:
        research_answer(
            symbol="RELIANCE",
            question="What is the last close?",
            history_loader=_history_loader(_frame(sessions=29)),
            forecast_loader=_forecast_loader,
            sentiment_loader=_sentiment_loader,
        )
    assert error.value.code == "insufficient_history"


def test_too_short_question_raises() -> None:
    with pytest.raises(ResearchAssistantError) as error:
        research_answer(
            symbol="RELIANCE",
            question="hi",
            history_loader=_history_loader(_frame()),
            forecast_loader=_forecast_loader,
            sentiment_loader=_sentiment_loader,
        )
    assert error.value.code == "question_too_short"


def test_provider_configured_talks_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.setenv("STOCKPILOT_LLM_API_KEY", "secret-test-key")
    monkeypatch.setenv(PROVIDER_MODEL_ENV, "test-model")

    payload = {
        "choices": [{"message": {"content": "The last close was 140 in the stored history."}}]
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @property
        def content(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    captured: dict[str, object] = {}

    def fake_post(
        url: str, *, data: bytes, headers: dict[str, str], timeout: float
    ) -> FakeResponse:  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured["method"] = "POST"
        captured["body_json"] = json.loads(data)
        captured["auth"] = headers.get("Authorization")
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "post", fake_post)
    result = research_answer(
        symbol="RELIANCE",
        question="What is the last close?",
        history_loader=_history_loader(_frame()),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
    )
    assert result["configured"] is True
    assert result["refused"] is False
    assert "140" in result["answer"]
    assert str(captured["url"]).endswith("/chat/completions")
    assert captured["method"] == "POST"
    body = captured["body_json"]
    assert isinstance(body, dict)
    assert body["temperature"] == 0.0
    assert captured["auth"] == "Bearer secret-test-key"


def test_provider_http_401_maps_to_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.setenv(PROVIDER_MODEL_ENV, "test-model")

    import requests

    class FakeResponse:
        status_code = 401

    def failing(url: str, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
        error = requests.exceptions.HTTPError("401 Client Error")
        error.response = FakeResponse()  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(requests, "post", failing)
    result = research_answer(
        symbol="RELIANCE",
        question="What is the last close?",
        history_loader=_history_loader(_frame()),
        forecast_loader=_forecast_loader,
        sentiment_loader=_sentiment_loader,
    )
    assert result["refused"] is True
    assert result["refusal_code"] == "provider_auth_failed"


def test_source_figure_is_forecast_and_sentiment() -> None:
    frame = _frame(last_close=140.0)
    call_count: dict[str, int] = {"forecast": 0, "sentiment": 0}

    def forecast(symbol: str) -> dict[str, Any]:
        call_count["forecast"] += 1
        return {
            "forecast_median": 151.25,
            "forecast_low": 138.0,
            "forecast_high": 164.5,
            "forecast_status": "model_supported",
            "model_label": "trend-ensemble",
            "target_timestamp": "2025-02-01T00:00:00+00:00",
        }

    def sentiment(symbol: str) -> dict[str, Any]:
        call_count["sentiment"] += 1
        return {"snapshot_at": "2025-01-30T17:30:00+00:00", "label": "positive", "score": 0.4, "count": 3}

    facts = build_corpus(
        symbol="RELIANCE",
        history_loader=_history_loader(frame),
        forecast_loader=forecast,
        sentiment_loader=sentiment,
    )
    by_label = {fact.label: fact.value for fact in facts}
    assert by_label["forecast median"] == "151.25"
    assert by_label["forecast low"] == "138"
    assert by_label["forecast high"] == "164.5"
    assert by_label["latest stored sentiment"] == "positive"
    assert by_label["sentiment snapshots stored"] == "3"
    assert call_count == {"forecast": 1, "sentiment": 1}


def test_provider_status_hides_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROVIDER_BASE_URL_ENV, "https://example.test/v1")
    monkeypatch.setenv("STOCKPILOT_LLM_API_KEY", "super-secret")
    monkeypatch.setenv(PROVIDER_MODEL_ENV, "test-model")
    status = provider_status()
    assert status["configured"] is True
    assert status["api_key_set"] is True
    assert "super-secret" not in json.dumps(status)
    assert "api_key" not in status