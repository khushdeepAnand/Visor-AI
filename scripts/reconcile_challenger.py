#!/usr/bin/env python3
"""Reconcile scheduled CQR challenger against the fresh promotion gate."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any

from services.alert_transport import dispatch_operational_alert

def reconcile(gate: dict[str, Any], challenger: dict[str, Any], tolerance: float = .02) -> tuple[bool, str]:
    try:
        gc, cc = float(gate["coverage"]), float(challenger["coverage"])
        gq, cq = bool(gate["quantile_order_valid"]), bool(challenger["quantile_order_valid"])
    except (KeyError, TypeError, ValueError): return False, "coverage/order fields missing or invalid"
    if not gq or not cq: return False, "quantile order invalid (possible inverted interval)"
    if abs(gc - cc) > tolerance: return False, f"coverage divergence {abs(gc-cc):.4f} > {tolerance:.4f}"
    return True, "gate and challenger reconciled"

def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(); p.add_argument("gate", type=Path); p.add_argument("challenger", type=Path); p.add_argument("--tolerance", type=float, default=.02); a=p.parse_args(argv)
    try: gate=json.loads(a.gate.read_text()); challenger=json.loads(a.challenger.read_text())
    except Exception as exc:
        msg = f"unavailable report: {exc}"
        dispatch_operational_alert("coverage_divergence", msg)
        print(f"ALERT coverage_divergence: {msg}", file=sys.stderr)
        return 2
    ok,msg=reconcile(gate,challenger,a.tolerance)
    if not ok:
        dispatch_operational_alert("coverage_divergence", msg)
    print(msg if ok else f"ALERT coverage_divergence: {msg}", file=sys.stderr if not ok else sys.stdout)
    return 0 if ok else 1
if __name__ == "__main__": raise SystemExit(main())
