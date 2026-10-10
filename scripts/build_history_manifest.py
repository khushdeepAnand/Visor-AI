"""Inventory all cached bars and export only verified real daily evidence."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from forecasting.history_manifest import build_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("cache/market_v6"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action-evidence", type=Path)
    args = parser.parse_args()
    result = build_manifest(args.cache, args.output, args.action_evidence)
    print(f"Inventoried {len(result['inventory'])} cache files; {sum(d['verified_rows'] for d in result['datasets'])} eligible daily rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
