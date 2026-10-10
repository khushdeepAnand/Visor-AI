"""Evaluate next-session challengers on sourced daily history, never demo data.

Input is a JSON manifest of datasets with symbol, provider, daily_csv, sha256,
adjustment_status='verified', optional signals_csv / intraday_csv / options_csv /
events_csv. CSV timestamp columns contain actual timezone-aware availability
timestamps. Daily bars must be completed sessions, not dates at midnight.
Without --input, report whether the local market cache has eligible real data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forecasting.next_day import evaluate_next_day, intraday_summaries, options_implied_move, VERSION
from forecasting.history_manifest import validate_dataset


def read_csv(path: Path, timestamp: str = "available_at") -> pd.DataFrame:
    frame = pd.read_csv(path)
    stamps = pd.to_datetime(frame.pop(timestamp))
    if stamps.dt.tz is None:
        raise ValueError(f"{path.name}: timestamps must include timezone")
    frame.index = pd.DatetimeIndex(stamps).tz_convert("UTC")
    return frame.sort_index()


def run(manifest: Path | None, *, sequence_candidate: bool = False, registry_uri: str | None = None) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    if manifest is None:
        inventory = []
        for path in sorted((root / "cache" / "market_v6").glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            attrs = payload.get("attrs", {})
            context = attrs.get("market_context", attrs.get("context", {}))
            provider = context.get("provider") or attrs.get("provider") or payload.get("provider") or "unknown"
            inventory.append({"file": path.name, "provider": provider,
                              "rows": len(payload.get("frame", {}).get("index", [])),
                              "eligible": False,
                              "reason": "Cache has no verified daily close availability/adjustment manifest"})
        return {"version": VERSION, "status": "blocked_missing_verified_held_out_data", "published": False,
                "inventory": inventory, "required": "Supply --input with sourced, adjusted daily data and availability timestamps. Demo/unknown sources cannot prove improvement."}
    evidence = json.loads(manifest.read_text(encoding="utf-8"))
    entries = evidence["datasets"]
    if not entries:
        raise ValueError("Manifest must contain at least one dataset")
    reports = []
    for entry in entries:
        provider = str(entry.get("provider", "")).lower()
        if not provider or any(word in provider for word in ("demo", "synthetic", "test", "unknown")):
            raise ValueError("Held-out evidence requires an identified real provider")
        if entry.get("adjustment_status") != "verified":
            raise ValueError("Corporate-action adjustments must be verified before evaluation")
        path = manifest.parent / entry["daily_csv"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError("Daily input SHA-256 does not match provenance manifest")
        daily = read_csv(path)
        validate_dataset(evidence, entry, daily)
        signals = read_csv(manifest.parent / entry["signals_csv"]) if entry.get("signals_csv") else None
        if entry.get("intraday_csv"):
            summary = intraday_summaries(read_csv(manifest.parent / entry["intraday_csv"]))
            if not summary.empty:
                signals = summary if signals is None else signals.join(summary, how="outer")
        if entry.get("options_csv"):
            chain = pd.read_csv(manifest.parent / entry["options_csv"])
            rows = []
            for origin, bar in daily.iterrows():
                move = options_implied_move(chain, spot=float(bar.Close), as_of=origin)
                if move:
                    rows.append({"available_at": origin, "atm_move": move["atm_move"]})
            if rows:
                options = pd.DataFrame(rows).set_index("available_at")
                signals = options if signals is None else signals.join(options, how="outer")
        events = read_csv(manifest.parent / entry["events_csv"], "session_close") if entry.get("events_csv") else None
        try:
            report = evaluate_next_day(entry["symbol"], daily, signals=signals, events=events,
                                       sequence_candidate=sequence_candidate, registry_uri=registry_uri,
                                       evidence_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
        except ValueError as exc:
            reports.append({"symbol": entry["symbol"], "status": "blocked_insufficient_evidence", "reason": str(exc),
                            "verified_rows": len(daily), "published": False})
            continue
        report["provenance"] = {"provider": entry["provider"], "sha256": digest, "adjustment_status": "verified"}
        reports.append(report)
    evaluated = [report for report in reports if "candidates" in report]
    return {"version": VERSION, "status": "evaluated_research_only" if evaluated else "blocked_insufficient_verified_history", "published": False, "reports": reports,
            "scope": "Per-symbol paired held-out reports; no cross-universe promotion inferred from a single symbol."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sequence-candidate", action="store_true")
    parser.add_argument("--registry-uri", help="MLflow tracking/registry URI; research artifacts only")
    args = parser.parse_args()
    try:
        report = run(args.input, sequence_candidate=args.sequence_candidate, registry_uri=args.registry_uri)
    except (ValueError, KeyError, OSError) as exc:
        report = {"version": VERSION, "status": "blocked_invalid_evidence", "reason": str(exc), "published": False}
    def native(value: Any) -> Any:
        if isinstance(value, np.generic):
            return value.item()
        raise TypeError(f"Unsupported report value: {type(value).__name__}")
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False, default=native), encoding="utf-8")
    print(f"Next-day evaluation: {report['status']}; report: {args.output}")
    return 0 if report["status"] == "evaluated_research_only" else 2


if __name__ == "__main__":
    raise SystemExit(main())
