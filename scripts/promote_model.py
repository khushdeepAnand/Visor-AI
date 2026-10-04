#!/usr/bin/env python3
"""Promotion gate CLI: the command that decides what ships.

Runs the real promotion gate over per-tier evaluation results and writes the
production manifest **only** when every tier passes.  A failing gate exits
non-zero without touching the manifest, so CI/deploy pipelines can block the
release on this command instead of the gate being a library nobody calls.

Usage:
    python scripts/promote_model.py --eval-json eval.json --candidate v14-cqr \\
        --artifact-hash <git-sha-or-digest>
    python scripts/promote_model.py --check
    python scripts/promote_model.py --revoke --reason "coverage drift"

Exit codes:
    0  promoted / manifest active / revocation done
    1  gate blocked the promotion / manifest inactive
    2  bad input or usage error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forecasting.model_promotion import (  # noqa: E402
    GATE_VERSION,
    active_promotion_receipt,
    decide_promotion,
    manifest_path,
    manifest_status,
)
from forecasting.promotion_gate import PromotionGateConfig  # noqa: E402

REQUIRED_TIER_FIELDS = ("coverage", "mase", "winkler_score", "n_forecasts")


def _load_tier_results(path: Path) -> dict[str, SimpleNamespace]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"evaluation file not found: {path}")
    except ValueError as exc:
        raise SystemExit(f"evaluation file is not valid JSON: {exc}")
    if not isinstance(raw, dict) or not raw:
        raise SystemExit("evaluation file must be a non-empty object keyed by tier")

    results: dict[str, SimpleNamespace] = {}
    for tier, entry in raw.items():
        if not isinstance(entry, dict):
            raise SystemExit(f"tier {tier!r} must be an object")
        missing = [key for key in REQUIRED_TIER_FIELDS if key not in entry]
        if missing:
            raise SystemExit(f"tier {tier!r} is missing fields: {', '.join(missing)}")
        dm = entry.get("diebold_mariano")
        results[str(tier)] = SimpleNamespace(
            coverage=float(entry["coverage"]),
            target_coverage=float(entry.get("target_coverage", 0.80)),
            mase=float(entry["mase"]),
            winkler_score=float(entry["winkler_score"]),
            baseline_winkler=entry.get("baseline_winkler"),
            n_forecasts=int(entry["n_forecasts"]),
            conditional_coverage=entry.get("conditional_coverage"),
            pinball_losses=entry.get("pinball_losses"),
            diebold_mariano=SimpleNamespace(
                reject_null=bool(dm.get("reject_null")),
                p_value=float(dm.get("p_value", 1.0)),
            )
            if isinstance(dm, dict)
            else None,
        )
    return results


def _cmd_promote(args: argparse.Namespace) -> int:
    try:
        tier_results = _load_tier_results(Path(args.eval_json))
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    decision = decide_promotion(
        tier_results,
        candidate_id=args.candidate,
        artifact_hash=args.artifact_hash,
        config=PromotionGateConfig(min_forecasts_per_tier=args.min_forecasts),
        target_coverage=args.target_coverage,
        manifest=Path(args.manifest) if args.manifest else None,
    )
    payload = {
        "candidate_id": decision.candidate_id,
        "passed": decision.passed,
        "written": decision.written,
        "reason": decision.reason,
        "manifest_path": decision.manifest_path,
        "failed_tiers": list(decision.failed_tiers),
        "gate_version": GATE_VERSION,
    }
    print(json.dumps(payload, indent=2))
    if not decision.passed:
        print("PROMOTION BLOCKED: production manifest unchanged.", file=sys.stderr)
        return 1
    print(f"PROMOTED: production manifest updated at {decision.manifest_path}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.manifest) if args.manifest else None
    status = manifest_status(target)
    print(json.dumps(status, indent=2))
    return 0 if status["active"] else 1


def _cmd_revoke(args: argparse.Namespace) -> int:
    target = Path(args.manifest) if args.manifest else manifest_path()
    if not target.exists():
        print(f"nothing to revoke: {target} does not exist", file=sys.stderr)
        return 0
    archive = target.with_suffix(".revoked.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["revoked"] = {"reason": args.reason, "revoked_by": "promote_model.py --revoke"}
    archive.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    target.unlink()
    print(f"revoked promotion manifest; audit copy at {archive}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    promote = sub.add_parser("promote", help="run the gate and promote only on pass (default)")
    promote.add_argument("--eval-json", required=True, help="per-tier evaluation results (JSON)")
    promote.add_argument("--candidate", required=True, help="candidate identifier, e.g. v14-cqr-r3")
    promote.add_argument("--artifact-hash", required=True, help="artifact digest identifying the candidate build")
    promote.add_argument("--target-coverage", type=float, default=0.80)
    promote.add_argument("--min-forecasts", type=int, default=100)
    promote.add_argument("--manifest", default=os.getenv("STOCKPILOT_PROMOTION_MANIFEST_PATH"))

    check = sub.add_parser("check", help="print manifest status; exit 1 when nothing valid is shipping")
    check.add_argument("--manifest", default=os.getenv("STOCKPILOT_PROMOTION_MANIFEST_PATH"))

    revoke = sub.add_parser("revoke", help="remove the production manifest (instant CQR rollback)")
    revoke.add_argument("--manifest", default=os.getenv("STOCKPILOT_PROMOTION_MANIFEST_PATH"))
    revoke.add_argument("--reason", required=True, help="why the promotion is being rolled back")

    args = parser.parse_args(argv)
    if args.command == "promote":
        return _cmd_promote(args)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "revoke":
        return _cmd_revoke(args)
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed, never crash mid-decision
        print(f"promotion command failed closed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
