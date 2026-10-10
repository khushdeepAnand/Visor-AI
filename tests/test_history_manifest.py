from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from forecasting.history_manifest import build_manifest, validate_dataset
from forecasting.corporate_actions import CorporateAction, CorporateActionType, verify_adjustment_correctness
from services.market_data.redis_cache import history_to_json
from scripts.evaluate_next_day import read_csv


def _cache(path: Path, provider: str = "upstox", symbol: str = "NIFTY 50") -> None:
    frame = pd.DataFrame({"Open": [100., 101.], "High": [102., 103.], "Low": [99., 100.],
                          "Close": [101., 102.], "Volume": [0., 0.]},
                         index=pd.date_range("2026-10-05", periods=2, tz="Asia/Kolkata"))
    frame.attrs.update(symbol=symbol, provider=provider, timeframe="1D", context={
        "provider": provider, "resolved_instrument_key": "NSE_INDEX|Nifty 50" if symbol == "NIFTY 50" else "NSE_EQ|issuer",
        "received_at": "2026-10-07T10:00:00+05:30"})
    path.write_text(history_to_json(frame), encoding="utf-8")


def test_manifest_excludes_demo_unreviewed_equity_and_tampered_rows(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache(cache / "real.json")
    _cache(cache / "demo.json", "demo")
    _cache(cache / "equity.json", symbol="RELIANCE")
    output = tmp_path / "manifest.json"
    manifest = build_manifest(cache, output)
    assert len(manifest["datasets"]) == 1
    assert sum(len(item["rows"]) for item in manifest["inventory"]) == 6
    daily = read_csv(tmp_path / manifest["datasets"][0]["daily_csv"])
    validate_dataset(manifest, manifest["datasets"][0], daily)
    assert daily.index[0].tz_convert("Asia/Kolkata").hour == 15
    with pytest.raises(ValueError, match="eligible manifest"):
        validate_dataset(manifest, manifest["datasets"][0], daily.iloc[:1])
    with pytest.raises(ValueError, match="Row-level"):
        validate_dataset({"datasets": []}, manifest["datasets"][0], daily)
    equities = next(item for item in manifest["inventory"] if item["symbol"] == "RELIANCE")
    assert not any(row["corporate_actions_verified_applied"] for row in equities["rows"])
    payload = json.loads((cache / "equity.json").read_text())
    payload["attrs"]["context"]["resolved_instrument_key"] = "NSE_INDEX|Nifty 50"
    (cache / "equity.json").write_text(json.dumps(payload))
    mismatch = build_manifest(cache, output)
    equity = next(item for item in mismatch["inventory"] if item["symbol"] == "RELIANCE")
    assert not equity["adjustment_verification"]["verified"]


def test_empty_calendar_and_duplicate_cache_fail_closed(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache(cache / "a.json")
    _cache(cache / "b.json")
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    assert len(manifest["datasets"]) == 1
    assert manifest["inventory"][1]["rows"][0]["exclusion_reasons"] == ["duplicate_session"]
    payload = json.loads((cache / "b.json").read_text())
    payload["frame"]["index"] = ["2024-10-07T00:00:00+05:30", "2024-10-08T00:00:00+05:30"]
    (cache / "b.json").write_text(json.dumps(payload))
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    assert manifest["inventory"][1]["rows"][0]["session_close_available_at"] is None


def test_incomplete_actions_cannot_be_verified():
    frame = pd.DataFrame({"Close": [100., 100., 100.]}, index=pd.date_range("2026-01-05", periods=3))
    action = CorporateAction("TEST", CorporateActionType.SPLIT, frame.index[1].date())
    assert not verify_adjustment_correctness(frame, "TEST", known_actions=[action])["verified"]
    mismatch = CorporateAction("OTHER", CorporateActionType.DIVIDEND, frame.index[1].date(), dividend_per_share=1)
    assert not verify_adjustment_correctness(frame, "TEST", known_actions=[mismatch])["verified"]


def test_index_volume_is_missing_signal_not_missing_origin():
    from forecasting.next_day import build_features
    frame = pd.DataFrame({"Open": 100., "High": 102., "Low": 99., "Close": 101., "Volume": 0.},
                         index=pd.date_range("2026-01-05", periods=60, tz="Asia/Kolkata"))
    features = build_features(frame)
    assert len(features.dropna()) == 38
    assert features.volume_ratio.eq(0).all()
    assert features.volume_ratio_available.eq(0).all()


def test_overlapping_cache_files_merge_new_sessions_and_quarantine_conflicts(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache(cache / "a.json")
    _cache(cache / "b.json")
    payload = json.loads((cache / "b.json").read_text())
    payload["attrs"]["context"]["received_at"] = "2026-10-09T10:00:00+05:30"
    payload["frame"]["index"] = ["2026-10-07T00:00:00+05:30", "2026-10-08T00:00:00+05:30"]
    (cache / "b.json").write_text(json.dumps(payload))
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    assert len(manifest["datasets"]) == 1
    assert manifest["datasets"][0]["verified_rows"] == 4
    daily = read_csv(tmp_path / manifest["datasets"][0]["daily_csv"])
    validate_dataset(manifest, manifest["datasets"][0], daily)
    payload["frame"]["index"][0] = "2026-10-06T00:00:00+05:30"
    payload["frame"]["data"][0] = [200., 202., 199., 201., 0.]
    (cache / "b.json").write_text(json.dumps(payload))
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    conflict_rows = [row for item in manifest["inventory"] for row in item["rows"] if row["date"] == "2026-10-06"]
    assert all(not row["eligible"] and "conflicting_session" in row["exclusion_reasons"] for row in conflict_rows)


def test_merge_cannot_restore_disconnected_history_from_older_cache(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache(cache / "a.json")
    _cache(cache / "b.json")
    newer = json.loads((cache / "a.json").read_text())
    newer["attrs"]["context"]["received_at"] = "2025-10-24T10:00:00+05:30"
    newer["frame"]["index"] = ["2025-10-20T00:00:00+05:30", "2025-10-21T00:00:00+05:30", "2025-10-23T00:00:00+05:30"]
    newer["frame"]["data"].append(newer["frame"]["data"][1])
    older = json.loads((cache / "b.json").read_text())
    older["attrs"]["context"]["received_at"] = "2025-10-24T10:00:00+05:30"
    older["frame"]["index"] = ["2025-10-17T00:00:00+05:30", "2025-10-20T00:00:00+05:30"]
    older["frame"]["data"][1] = newer["frame"]["data"][0]
    (cache / "a.json").write_text(json.dumps(newer))
    (cache / "b.json").write_text(json.dumps(older))
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    daily = read_csv(tmp_path / manifest["datasets"][0]["daily_csv"])
    assert len(daily) == 1 and str(daily.index[0].date()) == "2025-10-23"
    validate_dataset(manifest, manifest["datasets"][0], daily)


def test_missing_regular_session_cannot_be_a_one_session_label(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _cache(cache / "a.json")
    payload = json.loads((cache / "a.json").read_text())
    payload["attrs"]["context"]["received_at"] = "2026-10-08T10:00:00+05:30"
    payload["frame"]["index"] = ["2026-10-05T00:00:00+05:30", "2026-10-07T00:00:00+05:30"]
    (cache / "a.json").write_text(json.dumps(payload))
    manifest = build_manifest(cache, tmp_path / "manifest.json")
    assert manifest["datasets"][0]["verified_rows"] == 1
    assert manifest["datasets"][0]["missing_or_unverified_sessions_before_cutoff"] == ["2026-10-06"]
