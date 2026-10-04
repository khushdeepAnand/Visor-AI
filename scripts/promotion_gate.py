#!/usr/bin/env python3
"""Fail-closed promotion gate for offline CI artifacts.

The live replay gate is intentionally separate: CI may only promote when a
complete, machine-readable report is supplied. Missing, malformed, or
incomplete reports are failures (never an implicit pass).
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

REQUIRED = ("coverage", "quantile_order_valid", "sample_count")

def validate_report(payload: object, *, min_samples: int = 30) -> tuple[bool, list[str]]:
    if not isinstance(payload, dict): return False, ["report is not an object"]
    reasons = [f"missing {key}" for key in REQUIRED if key not in payload]
    if reasons: return False, reasons
    try:
        coverage = float(payload["coverage"]); samples = int(payload["sample_count"])
    except (TypeError, ValueError): return False, ["coverage/sample_count are not numeric"]
    if not 0 <= coverage <= 1: reasons.append("coverage outside [0,1]")
    if samples < min_samples: reasons.append(f"sample_count {samples} below minimum {min_samples}")
    if payload["quantile_order_valid"] is not True: reasons.append("quantile order invalid")
    if payload.get("status") not in (None, "pass", "passed"): reasons.append("report status is not pass")
    return not reasons, reasons

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("report", type=Path); parser.add_argument("--min-samples", type=int, default=30)
    args = parser.parse_args(argv)
    try: payload = json.loads(args.report.read_text(encoding="utf-8"))
    except Exception as exc: print(f"PROMOTION GATE FAILED: cannot read report: {exc}", file=sys.stderr); return 1
    ok, reasons = validate_report(payload, min_samples=args.min_samples)
    print("PROMOTION GATE PASSED" if ok else "PROMOTION GATE FAILED" + (": " + "; ".join(reasons) if reasons else ""))
    return 0 if ok else 1
if __name__ == "__main__": raise SystemExit(main())
