"""Fail-closed, row-level market evidence inventory. Never bless an empty action feed."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from forecasting.corporate_actions import CorporateAction, CorporateActionType, apply_corporate_actions, verify_adjustment_correctness
from services.market_calendar import CLOSE, IST, resolve_year_calendar
from services.market_data.redis_cache import history_from_json
from services.market_data.quality import validate_prices

SCHEMA = "stockpilot-history-evidence-v1"


def _regular_session(day: date) -> bool:
    calendar = resolve_year_calendar(day.year)
    return bool(calendar["verified"] and ((day.weekday() < 5 and day not in calendar["holidays"]) or day == date(2026, 2, 1) and day in calendar["special_sessions"]) and day != date(2026, 11, 8))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _action_evidence(frame: pd.DataFrame, symbol: str, context: dict[str, Any], evidence: dict[str, Any] | None,
                     base: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    # These are exchange indices, not issuer shares. Their provider series must
    # not receive equity split/dividend adjustments a second time.
    index_key = {"NIFTY 50": "NSE_INDEX|Nifty 50", "INDIA VIX": "NSE_INDEX|India VIX"}.get(symbol)
    if index_key and context.get("resolved_instrument_key") == index_key:
        check = verify_adjustment_correctness(frame, symbol, known_actions=[])
        return frame, {**check, "basis": "not_applicable_exchange_index", "source": context["resolved_instrument_key"]}
    if not evidence:
        return frame, {"verified": False, "basis": "missing_complete_sourced_action_coverage"}
    if not {"split", "bonus", "rights", "dividend"}.issubset(set(evidence.get("covered_action_types", []))):
        return frame, {"verified": False, "basis": "incomplete_action_type_coverage"}
    source = base / evidence["source_file"]
    if sha256(source) != evidence["source_sha256"] or not evidence.get("reviewed_by") or not evidence.get("source"):
        raise ValueError("Action evidence requires matching source hash, source and reviewer")
    if date.fromisoformat(evidence["complete_from"]) > frame.index.min().date() or date.fromisoformat(evidence["complete_through"]) < frame.index.max().date():
        return frame, {"verified": False, "basis": "action_feed_does_not_cover_history"}
    actions = []
    for row in evidence["actions"]:
        values = dict(row)
        values["action_type"] = CorporateActionType(values["action_type"])
        values["ex_date"] = date.fromisoformat(values["ex_date"])
        actions.append(CorporateAction(**values))
    state = evidence["input_adjustment"]
    if state not in {"raw", "already_adjusted"}:
        raise ValueError("Explicit raw/already_adjusted input state required")
    if state == "raw" and frame.attrs.get("corporate_action_events_applied"):
        raise ValueError("Cache already carries applied events; cannot adjust it again as raw")
    # Forecasting owns all adjustment and verification semantics. Normalize the
    # dates here because its event masks operate on exchange session dates.
    dated = frame.copy()
    dated.index = pd.DatetimeIndex(frame.index).tz_convert(IST).tz_localize(None).normalize()
    instrument = str(context.get("resolved_instrument_key", ""))
    if instrument.startswith("NSE_EQ|INE"):
        dated.attrs["isin"] = instrument.split("|", 1)[1]
    result = apply_corporate_actions(dated, symbol, actions=actions)
    check = verify_adjustment_correctness(dated, symbol, known_actions=actions, already_adjusted=state == "already_adjusted")
    check.update({"basis": "reviewed_complete_action_feed", "source": evidence["source"],
                  "source_sha256": evidence["source_sha256"], "reviewed_by": evidence["reviewed_by"]})
    adjusted = frame.copy()
    if state == "raw":
        adjusted.loc[:, :] = result.adjusted_frame.to_numpy()
    return adjusted, check


def build_manifest(cache: Path, output: Path, evidence_path: Path | None = None) -> dict[str, Any]:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path else {"datasets": []}
    supplied = {row["cache_sha256"]: row for row in evidence["datasets"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], str] = {}
    exports: dict[str, list[tuple[pd.DataFrame, str]]] = {}
    cached = [(path, history_from_json(path.read_text(encoding="utf-8"))) for path in sorted(cache.glob("*.json"))]
    versions: dict[tuple[str, str], set[str]] = {}
    for _, frame in cached:
        attrs = frame.attrs
        context = attrs.get("context", {})
        if attrs.get("provider") == context.get("provider") == "upstox" and attrs.get("timeframe") == "1D" and not any(word in str(attrs.get("source", "")).lower() for word in ("demo", "synthetic")):
            for stamp, bar in frame.iterrows():
                key = (str(attrs.get("symbol")), pd.Timestamp(str(stamp)).tz_convert(IST).date().isoformat())
                versions.setdefault(key, set()).add(json.dumps(bar.to_dict(), sort_keys=True))
    conflicts = {key for key, values in versions.items() if len(values) > 1}
    for path, frame in cached:
        digest = sha256(path)
        attrs = frame.attrs
        context = attrs.get("context", {})
        symbol = str(attrs.get("symbol", "unknown"))
        declarations = [str(attrs.get("provider", "")).lower(), str(context.get("provider", "")).lower()]
        demo = any(word in str(attrs.get("source", "")).lower() for word in ("demo", "synthetic")) or "offline_demo" in str(context).lower() or "demo" in declarations
        real = not demo and declarations == ["upstox", "upstox"] and bool(context.get("resolved_instrument_key"))
        provenance = "demo_synthetic" if demo else "real_upstox" if real else "unknown"
        daily = attrs.get("timeframe") == "1D"
        item = supplied.get(digest)
        if item and item.get("symbol") != symbol:
            raise ValueError("Action evidence symbol does not match cache")
        if pd.DatetimeIndex(frame.index).tz is None:
            raise ValueError(f"{path.name}: cache timestamps lack timezone")
        adjusted, check = _action_evidence(frame, symbol, context, item, evidence_path.parent if evidence_path else output.parent) if real and daily else (frame, {"verified": False, "basis": "not_real_daily_history"})
        quality_ok = True
        try:
            validate_prices(adjusted)
        except ValueError:
            quality_ok = False
        rows: list[dict[str, Any]] = []
        selected: list[int] = []
        for offset, stamp in enumerate(frame.index):
            day = stamp.tz_convert(IST).date()
            # The bundled Budget session uses regular NSE hours. Muhurat timing
            # is deliberately not guessed from the ordinary closing schedule.
            regular = _regular_session(day)
            close = pd.Timestamp.combine(day, CLOSE).tz_localize(IST)
            received = pd.Timestamp(str(context.get("received_at") or attrs.get("fetched_at") or "NaT"))
            completed = received.tzinfo is not None and received >= close
            reasons = []
            if not quality_ok:
                reasons.append("failed_price_quality_validation")
            if not real:
                reasons.append(provenance)
            if not daily:
                reasons.append("intraday_not_daily_evidence")
            if not check["verified"]:
                reasons.append(str(check["basis"]))
            if not regular:
                reasons.append("session_calendar_unverified_or_nonregular")
            if not completed:
                reasons.append("session_not_completed_at_fetch")
            if (symbol, day.isoformat()) in conflicts:
                reasons.append("conflicting_session")
            eligible = not reasons
            if eligible:
                key = (symbol, day.isoformat())
                values = json.dumps(adjusted.iloc[offset].to_dict(), sort_keys=True)
                if key in seen:
                    reasons.append("duplicate_session" if seen[key] == values else "conflicting_session")
                    eligible = False
                else:
                    seen[key] = values
                    selected.append(offset)
            rows.append({"symbol": symbol, "date": day.isoformat(), "bar_timestamp": stamp.isoformat(),
                         "provenance": provenance, "corporate_actions_verified_applied": bool(real and check["verified"]),
                         "adjustment_verified_through": day.isoformat() if real and check["verified"] else None,
                         "session_close_available_at": close.isoformat() if regular else None,
                         "availability_basis": "verified_exchange_schedule_bar_completion" if regular else "unverified",
                         "provider_received_at": received.isoformat(), "eligible": eligible, "exclusion_reasons": reasons})
        # Do not turn two sessions into a one-session target by concatenating
        # history across an observed session with unknown completion time.
        if selected:
            gap_offsets = [i for i, row in enumerate(rows) if i < selected[-1] and any(reason in row["exclusion_reasons"] for reason in ("session_calendar_unverified_or_nonregular", "conflicting_session"))]
            if gap_offsets:
                cutoff = gap_offsets[-1]
                for i in selected:
                    if i <= cutoff:
                        rows[i]["eligible"] = False
                        rows[i]["exclusion_reasons"].append("history_before_unverified_session_gap")
                selected = [i for i in selected if i > cutoff]
        inventory.append({"cache_file": path.name, "cache_sha256": digest, "symbol": symbol,
                          "timeframe": attrs.get("timeframe"), "provenance": provenance, "adjustment_verification": check, "rows": rows})
        if selected:
            export = adjusted.iloc[selected].copy()
            export.index = pd.DatetimeIndex([rows[i]["session_close_available_at"] for i in selected])
            export.index.name = "available_at"
            exports.setdefault(symbol, []).append((export, digest))
    for symbol, parts in sorted(exports.items()):
        export = pd.concat([part[0] for part in parts]).sort_index()
        start, end = export.index.min().date(), export.index.max().date()
        observed = set(pd.DatetimeIndex(export.index).tz_convert(IST).date)
        gap_days = [stamp.date() for stamp in pd.date_range(start, end) if _regular_session(stamp.date()) and stamp.date() not in observed]
        gap_days.extend(date.fromisoformat(row["date"]) for item in inventory if item["symbol"] == symbol and item["provenance"] == "real_upstox" and item["timeframe"] == "1D" for row in item["rows"] if start <= date.fromisoformat(row["date"]) < end and any(reason in row["exclusion_reasons"] for reason in ("session_calendar_unverified_or_nonregular", "conflicting_session")))
        if gap_days:
            cutoff_day = max(gap_days)
            export = export[pd.DatetimeIndex(export.index).tz_convert(IST).date > cutoff_day]
            for item in inventory:
                if item["symbol"] == symbol:
                    for row in item["rows"]:
                        if row["eligible"] and date.fromisoformat(row["date"]) <= cutoff_day:
                            row["eligible"] = False
                            row["exclusion_reasons"].append("history_before_merged_session_gap")
        target = output.parent / (re.sub(r"[^A-Za-z0-9_-]", "-", symbol) + "-verified-daily.csv")
        export.to_csv(target)
        datasets.append({"symbol": symbol, "provider": "upstox", "adjustment_status": "verified",
                         "daily_csv": target.name, "sha256": sha256(target), "cache_sha256s": [part[1] for part in parts],
                         "missing_or_unverified_sessions_before_cutoff": sorted({day.isoformat() for day in gap_days}),
                         "verified_rows": len(export)})
    result = {"schema": SCHEMA, "datasets": datasets, "inventory": inventory,
              "scope": "Local cache provenance, not independent provider authenticity audit; scheduled close is bar completion, not observed historical delivery latency."}
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    return result


def validate_dataset(manifest: dict[str, Any], entry: dict[str, Any], daily: pd.DataFrame) -> None:
    validate_prices(daily)
    if manifest.get("schema") != SCHEMA:
        raise ValueError("Row-level verified history manifest required; provider/verified strings alone are insufficient")
    sources = entry.get("cache_sha256s", [entry.get("cache_sha256")])
    inventories = [item for item in manifest["inventory"] if item["cache_sha256"] in sources]
    if len(inventories) != len(sources) or not inventories or any(item["provenance"] != "real_upstox" or not item["adjustment_verification"]["verified"] or item["symbol"] != entry["symbol"] for item in inventories):
        raise ValueError("Missing unique real cache provenance")
    stamps = sorted(pd.Timestamp(row["session_close_available_at"]).tz_convert("UTC") for item in inventories for row in item["rows"] if row["eligible"] and row["corporate_actions_verified_applied"] and row["provenance"] == "real_upstox" and not row["exclusion_reasons"])
    if not daily.index.equals(pd.DatetimeIndex(stamps)) or not daily.index.is_unique:
        raise ValueError("Daily rows do not match eligible manifest subset")
