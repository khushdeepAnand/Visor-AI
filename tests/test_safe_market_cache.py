from __future__ import annotations

import pandas as pd
import pytest

from services.market_data.redis_cache import history_from_json, history_to_json


def test_history_json_round_trip_preserves_data_and_context() -> None:
    frame = pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0], "Volume": [1234]},
        index=pd.to_datetime(["2026-08-28T10:00:00Z"]),
    )
    frame.attrs["context"] = {"provider": "upstox", "is_stale": False}
    restored = history_from_json(history_to_json(frame))
    assert restored.iloc[0].to_dict() == frame.iloc[0].to_dict()
    assert restored.attrs == frame.attrs


def test_history_json_rejects_non_json_executable_payload() -> None:
    with pytest.raises((UnicodeDecodeError, ValueError)):
        history_from_json(b"\x80\x04cos\nsystem\n.")
