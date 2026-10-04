"""Verify allowlisted release inputs, required documents, and secret hygiene."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_hygiene import collect_allowlisted, release_input_issues
from scripts.scan_secrets import scan_paths


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    issues = release_input_issues(ROOT)
    if issues:
        print("Release input hygiene failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1

    files = collect_allowlisted(ROOT)
    findings = scan_paths(files)
    if findings:
        print("Release input secret scan failed. Values are never printed.")
        for finding in findings:
            print(f"- {finding.render()}")
        return 1

    print(f"Release input gates passed: {len(files)} allowlisted files, 0 secret findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
