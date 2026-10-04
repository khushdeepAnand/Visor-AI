#!/usr/bin/env python3
"""Regenerate the Unreleased section from conventional commits.

This script is deterministic and reads only local git history. It never adds
commit messages containing credentials or arbitrary generated content.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
SECTION_ORDER = ("feat", "fix", "refactor", "perf", "docs", "test", "chore")
HEAD_RE = re.compile(r"^## \[?Unreleased\]?\s*$", re.IGNORECASE | re.MULTILINE)
COMMIT_RE = re.compile(r"^(feat|fix|refactor|perf|docs|test|chore)(?:\([^)]*\))?!?:\s+(.+)$", re.IGNORECASE)


def commits() -> dict[str, list[str]]:
    raw = subprocess.check_output(
        ["git", "log", "--format=%s", "-100", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    grouped: dict[str, list[str]] = {key: [] for key in SECTION_ORDER}
    for line in raw.splitlines():
        match = COMMIT_RE.match(line.strip())
        if not match:
            continue
        kind, subject = match.groups()
        subject = subject.strip()
        if subject and subject not in grouped[kind.lower()]:
            grouped[kind.lower()].append(subject)
    return grouped


def render_unreleased(grouped: dict[str, list[str]]) -> str:
    labels = {
        "feat": "Features", "fix": "Fixes", "refactor": "Refactoring",
        "perf": "Performance", "docs": "Documentation", "test": "Tests",
        "chore": "Maintenance",
    }
    lines = ["## Unreleased", ""]
    for kind in SECTION_ORDER:
        items = grouped[kind]
        if not items:
            continue
        lines.extend([f"### {labels[kind]}", *[f"- {item}" for item in items], ""])
    if len(lines) == 2:
        lines.append("No conventional commits recorded.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "# Changelog\n\n"
    section = render_unreleased(commits())
    match = HEAD_RE.search(text)
    if match:
        next_heading = re.search(r"^## (?!\[?Unreleased\]?\s*$).+$", text[match.end():], re.IGNORECASE | re.MULTILINE)
        end = match.end() + next_heading.start() if next_heading else len(text)
        text = text[:match.start()] + section + "\n" + text[end:]
    else:
        text = text.rstrip() + "\n\n" + section
    CHANGELOG.write_text(text.rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
