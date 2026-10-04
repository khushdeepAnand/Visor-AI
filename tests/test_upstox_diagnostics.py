from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from services.market_data.base import Instrument
from services.market_data.upstox import UpstoxProvider
from services.market_data.upstox_auth import UpstoxRequestError, decode_token_metadata, request_json


class Response:
    def __init__(self, status: int, payload: dict[str, object], headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> dict[str, object]:
        return self._payload


def _jwt(expiry: datetime) -> str:
    def encode(value: dict[str, object]) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return ".".join((encode({"alg": "none"}), encode({"exp": expiry.timestamp()}), "unsignedfixture"))


def _instrument() -> Instrument:
    return Instrument(
        symbol="RELIANCE",
        name="Reliance Industries",
        exchange="NSE",
        segment="NSE_EQ",
        instrument_type="EQ",
        instrument_key="NSE_EQ|INE002A01018",
    )


def test_token_metadata_does_not_claim_future_token_is_verified() -> None:
    metadata = decode_token_metadata(_jwt(datetime.now(timezone.utc) + timedelta(days=1)))
    assert metadata.configured is True
    assert metadata.is_expired is False
    assert metadata.verification == "not_checked"


def test_token_metadata_detects_local_expiry_without_exposing_token() -> None:
    metadata = decode_token_metadata(_jwt(datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert metadata.is_expired is True
    assert set(metadata.to_dict()) == {"configured", "expires_at", "is_expired", "verification"}


def test_upstox_quote_success_fixture_validates_returned_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = UpstoxProvider(token="fixture-token")
    monkeypatch.setattr(provider, "_instrument", lambda symbol: _instrument())
    payload = {
        "status": "success",
        "data": {
            "NSE_EQ:RELIANCE": {
                "instrument_token": "NSE_EQ|INE002A01018",
                "symbol": "RELIANCE",
                "last_price": 2500.0,
                "timestamp": "2026-08-28T15:29:00+05:30",
                "volume": 100,
                "ohlc": {"open": 2490, "high": 2510, "low": 2480, "close": 2495},
                "depth": {"buy": [], "sell": []},
            }
        },
    }
    monkeypatch.setattr("services.market_data.upstox_auth.requests.get", lambda *args, **kwargs: Response(200, payload))
    quote = provider.get_quote("RELIANCE")
    assert quote.symbol == "RELIANCE"
    assert quote.source == "Upstox"
    assert provider.last_diagnostics["quote"].classification == "valid"


def test_upstox_index_quote_accepts_placeholder_symbol_with_exact_token(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = UpstoxProvider(token="fixture-token")
    instrument = Instrument(
        symbol="SENSEX",
        name="BSE SENSEX",
        exchange="BSE",
        segment="BSE_INDEX",
        instrument_type="INDEX",
        instrument_key="BSE_INDEX|SENSEX",
    )
    monkeypatch.setattr(provider, "_instrument", lambda symbol: instrument)
    payload = {
        "status": "success",
        "data": {
            "BSE_INDEX:SENSEX": {
                "instrument_token": "BSE_INDEX|SENSEX",
                "symbol": "NA",
                "last_price": 76957.27,
                "timestamp": "2026-08-31T22:30:34.526+05:30",
            }
        },
    }
    monkeypatch.setattr("services.market_data.upstox_auth.requests.get", lambda *args, **kwargs: Response(200, payload))

    quote = provider.get_quote("SENSEX")

    assert quote.symbol == "SENSEX"
    assert quote.source == "Upstox"


def test_upstox_history_success_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = UpstoxProvider(token="fixture-token")
    monkeypatch.setattr(provider, "_instrument", lambda symbol: _instrument())
    payload = {
        "status": "success",
        "data": {"candles": [["2026-08-28T15:25:00+05:30", 100, 103, 99, 102, 1200, 0]]},
    }
    monkeypatch.setattr("services.market_data.upstox_auth.requests.get", lambda *args, **kwargs: Response(200, payload))
    history = provider.get_history("RELIANCE", "5m", "1w")
    assert len(history) == 1
    assert history.attrs["source"] == "Upstox"
    assert provider.last_diagnostics["history"].http_status == 200


@pytest.mark.parametrize(
    "quote_payload",
    [
        {"last_price": 2500.0, "timestamp": "2026-08-28T15:29:00+05:30"},
        {"instrument_token": "NSE_EQ|INE002A01018", "symbol": "RELIANCE", "last_price": 2500.0},
        {"instrument_token": "NSE_EQ|INE002A01018", "symbol": "RELIANCE", "last_price": 0, "timestamp": "2026-08-28T15:29:00+05:30"},
    ],
)
def test_upstox_quote_rejects_unverifiable_identity_timestamp_or_price(monkeypatch: pytest.MonkeyPatch, quote_payload: dict[str, object]) -> None:
    provider = UpstoxProvider(token="fixture-token")
    monkeypatch.setattr(provider, "_instrument", lambda symbol: _instrument())
    payload = {"status": "success", "data": {"unidentified": quote_payload}}
    monkeypatch.setattr("services.market_data.upstox_auth.requests.get", lambda *args, **kwargs: Response(200, payload))
    with pytest.raises(Exception):
        provider.get_quote("RELIANCE")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "endpoint_error"),
        (401, "revoked_or_invalid"),
        (403, "permission_denied"),
        (404, "endpoint_error"),
    ],
)
def test_non_retryable_http_classifications(status: int, expected: str) -> None:
    calls = 0

    def get(*args: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        return Response(status, {"errors": [{"errorCode": "UDAPI100011", "message": "sanitized fixture"}]})

    with pytest.raises(UpstoxRequestError) as raised:
        request_json(endpoint="/fixture", url="https://api.upstox.com/fixture", token="fixture-token", timeout=1, getter=get)
    assert calls == 1
    assert raised.value.diagnostic.classification == expected
    assert raised.value.diagnostic.http_status == status
    assert raised.value.diagnostic.error_code == "UDAPI100011"
    assert "sanitized fixture" not in str(raised.value)


def test_expired_401_uses_only_local_expiry_metadata() -> None:
    expired = _jwt(datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(UpstoxRequestError) as raised:
        request_json(
            endpoint="/fixture",
            url="https://api.upstox.com/fixture",
            token=expired,
            timeout=1,
            getter=lambda *args, **kwargs: Response(401, {}),
        )
    assert raised.value.diagnostic.classification == "expired"


def test_429_respects_retry_after_then_succeeds() -> None:
    responses = iter((Response(429, {}, {"Retry-After": "1.5"}), Response(200, {"status": "success"})))
    delays: list[float] = []
    payload, diagnostic = request_json(
        endpoint="/fixture",
        url="https://api.upstox.com/fixture",
        token="fixture-token",
        timeout=1,
        getter=lambda *args, **kwargs: next(responses),
        sleeper=delays.append,
    )
    assert payload["status"] == "success"
    assert diagnostic.attempts == 2
    assert delays == [1.5]


def test_terminal_429_is_classified_rate_limited() -> None:
    with pytest.raises(UpstoxRequestError) as raised:
        request_json(
            endpoint="/fixture",
            url="https://api.upstox.com/fixture",
            token="fixture-token",
            timeout=1,
            max_attempts=1,
            getter=lambda *args, **kwargs: Response(429, {}, {"Retry-After": "1"}),
        )
    assert raised.value.diagnostic.classification == "rate_limited"


def test_timeout_is_bounded_and_classified() -> None:
    calls = 0
    delays: list[float] = []

    def timeout(*args: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        raise requests.Timeout("fixture timeout")

    with pytest.raises(UpstoxRequestError) as raised:
        request_json(
            endpoint="/fixture",
            url="https://api.upstox.com/fixture",
            token="fixture-token",
            timeout=1,
            getter=timeout,
            sleeper=delays.append,
            jitter=lambda: 0,
        )
    assert calls == 3
    assert len(delays) == 2
    assert raised.value.diagnostic.classification == "timeout"


def test_5xx_retries_are_bounded() -> None:
    calls = 0

    def get(*args: object, **kwargs: object) -> Response:
        nonlocal calls
        calls += 1
        return Response(503, {"errors": [{"errorCode": "UDAPI_SERVICE"}]})

    with pytest.raises(UpstoxRequestError) as raised:
        request_json(
            endpoint="/fixture",
            url="https://api.upstox.com/fixture",
            token="fixture-token",
            timeout=1,
            getter=get,
            sleeper=lambda delay: None,
            jitter=lambda: 0,
        )
    assert calls == 3
    assert raised.value.diagnostic.classification == "endpoint_error"
    assert raised.value.diagnostic.http_status == 503


def test_network_error_is_classified_without_exception_text() -> None:
    def fail(*args: object, **kwargs: object) -> Response:
        raise requests.ConnectionError("credential-shaped-text-must-not-escape")

    with pytest.raises(UpstoxRequestError) as raised:
        request_json(
            endpoint="/fixture",
            url="https://api.upstox.com/fixture",
            token="fixture-token",
            timeout=1,
            getter=fail,
            sleeper=lambda delay: None,
        )
    assert raised.value.diagnostic.classification == "network_error"
    assert "credential-shaped" not in str(raised.value)


def test_access_only_headers_never_fall_back_to_analytics_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UPSTOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "analytics-fixture")
    provider = UpstoxProvider()
    with pytest.raises(Exception, match="UPSTOX_ACCESS_TOKEN is not configured"):
        _ = provider.access_headers
