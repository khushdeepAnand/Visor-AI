#!/usr/bin/env python3
"""Check that all required documents exist in the source tree and release allowlist."""

from __future__ import annotations

import sys
from pathlib import Path


REQUIRED_DOCS = {
    "ADMIN_OPERATIONS.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    "DATA_RETENTION_AND_DELETION.md",
    "DEPLOYMENT.md",
    "DESIGN_SYSTEM.md",
    "FORECAST_CONTRACT.md",
    "MODEL_CARD.md",
    "QUICK_START_WINDOWS.md",
    "README.md",
    "REFERENCE_RECONCILIATION.md",
    "REGULATORY_REVIEW_REQUIRED.md",
    "NO_LIVE_ORDER_EXECUTION_POLICY.md",
    "RELEASE_CHECKLIST.md",
    "SECURITY.md",
    "SECURITY_GOVERNANCE.md",
    "SECURITY_REVIEW.md",
    "TESTING.md",
    "THREAT_MODEL.md",
    "UPGRADE_VERIFICATION.md",
    "UPSTOX_CONFIGURATION.md",
    "VERIFICATION_RESULTS.md",
}


def check_docs_exist(root: Path) -> tuple[list[str], list[str]]:
    """Check if all required docs exist in the source tree."""
    missing = []
    present = []
    for doc in sorted(REQUIRED_DOCS):
        path = root / doc
        if path.is_file():
            present.append(doc)
        else:
            missing.append(doc)
    return missing, present


def check_allowlist(root: Path) -> tuple[list[str], list[str]]:
    """Check if all required docs are in release-allowlist.txt."""
    allowlist_path = root / "release-allowlist.txt"
    if not allowlist_path.is_file():
        return list(REQUIRED_DOCS), []

    lines = allowlist_path.read_text(encoding="utf-8").splitlines()
    allowed = set()
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            allowed.add(line)

    missing = []
    present = []
    for doc in sorted(REQUIRED_DOCS):
        if doc in allowed:
            present.append(doc)
        else:
            missing.append(doc)
    return missing, present


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    print("=" * 60)
    print("Required Documents Check")
    print("=" * 60)

    # Check source tree
    missing_tree, present_tree = check_docs_exist(root)
    print(f"\nSource tree check ({len(present_tree)}/{len(REQUIRED_DOCS)} present):")
    if missing_tree:
        for doc in missing_tree:
            print(f"  MISSING: {doc}")
    else:
        print("  All required documents present in source tree.")

    # Check allowlist
    missing_allowlist, present_allowlist = check_allowlist(root)
    print(f"\nRelease allowlist check ({len(present_allowlist)}/{len(REQUIRED_DOCS)} listed):")
    if missing_allowlist:
        for doc in missing_allowlist:
            print(f"  MISSING from allowlist: {doc}")
    else:
        print("  All required documents listed in release-allowlist.txt.")

    # Summary
    all_ok = not missing_tree and not missing_allowlist
    print("\n" + "=" * 60)
    if all_ok:
        print("PASS: All required documents verified.")
        return 0
    else:
        print("FAIL: Required documents missing.")
        return 1


if __name__ == "__main__":
    sys.exit(main())