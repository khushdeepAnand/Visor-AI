"""Fail closed when release input contains likely credential material."""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


# The bundled instrument catalogue is intentionally large. Files above this
# bounded limit fail closed instead of being silently skipped.
MAX_TEXT_BYTES = 100 * 1024 * 1024
# Dependency and VCS trees are not release inputs. Runtime/generated locations
# such as cache, logs, database, .next, reports and bytecode remain scan targets.
EXTERNAL_DIRECTORIES = {".git", ".venv", "node_modules", "venv"}
SENSITIVE_ENV_NAME = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET|API_KEY)$",
    re.IGNORECASE,
)
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$")
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}(?![A-Za-z0-9_-])")),
    ("aws_access_key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("github_token", re.compile(r"(?<![A-Za-z0-9_])gh[opsu]_[A-Za-z0-9]{30,}(?![A-Za-z0-9_])")),
)
PLACEHOLDERS = {"", "''", '""', "none", "null", "changeme", "replace_me"}


@dataclass(frozen=True)
class Finding:
    location: str
    line: int
    kind: str

    def render(self) -> str:
        return f"{self.location}:{self.line}: {self.kind}"


def _looks_populated(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if normalized in PLACEHOLDERS:
        return False
    return not normalized.startswith(("<", "${", "%"))


def _is_known_test_fixture(location: str, name: str, value: str) -> bool:
    filename = location.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = value.strip().strip("'\"")
    return filename.startswith("test_") and filename.endswith(".py") and name == "PASSWORD" and normalized == "StrongPass9!x"


def scan_text(text: str, location: str) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        assignment = ENV_ASSIGNMENT.match(line)
        if assignment and SENSITIVE_ENV_NAME.search(assignment.group(1)):
            if _looks_populated(assignment.group(2)) and not _is_known_test_fixture(
                location, assignment.group(1), assignment.group(2)
            ):
                findings.append(Finding(location, line_number, "populated_sensitive_environment_variable"))
        for kind, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(Finding(location, line_number, kind))
    return findings


def _decode(data: bytes) -> str | None:
    if len(data) > MAX_TEXT_BYTES:
        return None
    return data.decode("utf-8", errors="ignore").replace("\x00", "\n")


def _scan_zip(path: Path, display: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if member.file_size > MAX_TEXT_BYTES:
                    findings.append(Finding(f"{display}!{member.filename}", 0, "unscanned_oversized_file"))
                    continue
                text = _decode(archive.read(member))
                if text is not None:
                    findings.extend(scan_text(text, f"{display}!{member.filename}"))
    except (OSError, zipfile.BadZipFile) as error:
        findings.append(Finding(display, 0, f"unreadable_zip:{type(error).__name__}"))
    return findings


def scan_paths(paths: Iterable[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for supplied in paths:
        path = supplied.resolve()
        if path.is_file():
            candidates: Iterable[Path] = [path]
        else:
            discovered: list[Path] = []
            for directory, names, files in os.walk(path):
                names[:] = [name for name in names if name not in EXTERNAL_DIRECTORIES]
                discovered.extend(Path(directory) / filename for filename in files)
            candidates = discovered
        for candidate in candidates:
            display = str(candidate.relative_to(path)) if path.is_dir() else candidate.name
            if candidate.suffix.lower() == ".zip":
                findings.extend(_scan_zip(candidate, display))
                continue
            try:
                data = candidate.read_bytes()
            except OSError as error:
                findings.append(Finding(display, 0, f"unreadable_file:{type(error).__name__}"))
                continue
            text = _decode(data)
            if text is None:
                findings.append(Finding(display, 0, "unscanned_oversized_file"))
                continue
            if text is not None:
                findings.extend(scan_text(text, display))
    return findings


def env_policy_issues(root: Path) -> list[Finding]:
    """Check that non-example local environment files contain only placeholders.

    Private environment files (``.env``, ``frontend/.env.local``) must not hold
    real secret values when a release is being built. The packaged archive may
    include a ``.env.example`` skeleton only. A build that intentionally ships a
    populated local environment file must opt out explicitly with an override
    flag so the exception is a visible, deliberate decision rather than drift.
    """
    findings: list[Finding] = []
    for location in (root / ".env", root / "frontend" / ".env.local"):
        if not location.is_file():
            continue
        try:
            text = location.read_text(encoding="utf-8")
        except OSError as error:
            findings.append(Finding(str(location), 0, f"unreadable_file:{type(error).__name__}"))
            continue
        findings.extend(scan_text(text, str(location)))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    findings = scan_paths(args.paths)
    if findings:
        print("Secret scan failed. Values are never printed.", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.render()}", file=sys.stderr)
        return 1
    print(f"Secret scan passed: {len(args.paths)} path(s), 0 findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
