"""Validate and inventory the explicit StockPilot release payload."""
from __future__ import annotations

import argparse
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


PROJECT_FOLDER = "StockPilot-AI-v18"
DENIED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "blob-report",
    "cache",
    "database",
    "dist",
    "htmlcov",
    "logs",
    "node_modules",
    "out",
    "playwright-report",
    "reports",
    "screenshots",
    "test-results",
    "venv",
}
DENIED_FILENAMES = {".coverage", ".ds_store", "cachedir.tag", "thumbs.db"}
DENIED_SUFFIXES = {
    ".db",
    ".h5",
    ".joblib",
    ".keras",
    ".log",
    ".onnx",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
    ".temp",
    ".tmp",
    ".tsbuildinfo",
    ".zip",
}
REQUIRED_DOCUMENTS = {
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
REQUIRED_LAUNCHERS = {
    "CONFIGURE_UPSTOX.bat",
    "RUN_STOCKPILOT.bat",
    "SETUP_STOCKPILOT.bat",
    "START_STOCKPILOT.bat",
    "VERIFY_STOCKPILOT.bat",
    "VERIFY_UPSTOX_LIVE.bat",
    "configure_upstox.ps1",
    "run_stockpilot.ps1",
    "setup_stockpilot.ps1",
    "start_stockpilot.ps1",
    "verify_stockpilot.ps1",
    "verify_upstox_live.ps1",
}

REQUIRED_RUNTIME_FILES = {
    "frontend/scripts/generate-api-client.mjs",
    "alembic.ini",
    "alembic/env.py",
    "alembic/versions/8788046ff051_initial_postgresql_schema.py",
}


def path_issue(relative: str, *, allow_private_env: bool = False) -> str | None:
    path = PurePosixPath(relative.replace("\\", "/"))
    lowered_parts = tuple(part.lower() for part in path.parts)
    if any(part in DENIED_DIRECTORIES for part in lowered_parts):
        return "forbidden directory"
    name = lowered_parts[-1] if lowered_parts else ""
    if name in DENIED_FILENAMES:
        return "forbidden generated file"
    if name.startswith(".env") and not name.endswith(".example") and not (allow_private_env and path.as_posix() == ".env"):
        return "private environment file"
    if name.endswith((".db-shm", ".db-wal", ".sqlite-shm", ".sqlite-wal")):
        return "database sidecar file"
    if PurePosixPath(name).suffix.lower() in DENIED_SUFFIXES:
        return "forbidden file type"
    return None


def allowlist_patterns(root: Path) -> list[str]:
    allowlist = root / "release-allowlist.txt"
    return [
        line.strip()
        for line in allowlist.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def collect_allowlisted(root: Path, *, include_private_env: bool = False) -> list[Path]:
    selected: set[Path] = set()
    for pattern in allowlist_patterns(root):
        if pattern.endswith("/**"):
            base = root / pattern[:-3]
            candidates = base.rglob("*") if base.is_dir() else ()
        else:
            candidates = root.glob(pattern)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.is_symlink():
                raise ValueError(f"Symlinks are not release files: {candidate.relative_to(root)}")
            relative = candidate.relative_to(root).as_posix()
            if path_issue(relative) is None:
                selected.add(candidate)
    private_env = root / ".env"
    if include_private_env and private_env.is_file():
        selected.add(private_env)
    if not selected:
        raise ValueError("The release allowlist selected no files.")
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix().lower())


def _inventory_issues(actual: set[str], expected: Iterable[str] | None) -> list[str]:
    if expected is None:
        return []
    expected_set = {PurePosixPath(path.replace("\\", "/")).as_posix() for path in expected}
    issues = [f"missing inventory file: {path}" for path in sorted(expected_set - actual)]
    issues.extend(f"unexpected inventory file: {path}" for path in sorted(actual - expected_set))
    return issues


def documentation_issues(files: Iterable[str]) -> list[str]:
    actual = {PurePosixPath(path.replace("\\", "/")).as_posix() for path in files}
    return [f"missing required document: {name}" for name in sorted(REQUIRED_DOCUMENTS - actual)]


def release_input_issues(root: Path) -> list[str]:
    """Validate required release inputs without treating local build state as payload."""

    try:
        selected = collect_allowlisted(root)
    except (OSError, ValueError) as error:
        return [str(error)]
    relative = {path.relative_to(root).as_posix() for path in selected}
    issues = documentation_issues(relative)
    issues.extend(f"missing required runtime file: {name}" for name in sorted(REQUIRED_RUNTIME_FILES - relative))
    issues.extend(launcher_issues(root))
    issues.extend(
        f"{path}: {reason}"
        for path in sorted(relative)
        if (reason := path_issue(path)) is not None
    )
    return sorted(issues)


def launcher_issues(root: Path) -> list[str]:
    return [f"missing required launcher: {name}" for name in sorted(REQUIRED_LAUNCHERS) if not (root / name).is_file()]


def tree_issues(
    root: Path,
    expected_files: Iterable[str] | None = None,
    *,
    allow_private_env: bool = False,
) -> list[str]:
    issues: list[str] = []
    files: set[str] = set()
    casefolded: dict[str, str] = {}
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            issues.append(f"{candidate.relative_to(root).as_posix()}: symlink")
        elif candidate.is_file():
            relative = candidate.relative_to(root).as_posix()
            files.add(relative)
            folded = relative.casefold()
            previous = casefolded.setdefault(folded, relative)
            if previous != relative:
                issues.append(f"case-colliding tree paths: {previous!r} and {relative!r}")
            reason = path_issue(relative, allow_private_env=allow_private_env)
            if reason:
                issues.append(f"{relative}: {reason}")
    issues.extend(_inventory_issues(files, expected_files))
    issues.extend(documentation_issues(files))
    issues.extend(launcher_issues(root))
    return sorted(issues)


def _zip_member_issues(infos: list[zipfile.ZipInfo]) -> list[str]:
    issues: list[str] = []
    exact: set[str] = set()
    casefolded: dict[str, str] = {}
    for info in infos:
        name = info.filename
        normalized = name.replace("\\", "/")
        collision_name = normalized.rstrip("/")
        if not name or "\x00" in name:
            issues.append(f"{name!r}: empty or NUL-containing archive path")
            continue
        if name in exact:
            issues.append(f"{name!r}: duplicate archive member")
        exact.add(name)
        folded = collision_name.casefold()
        previous = casefolded.setdefault(folded, collision_name)
        if previous != collision_name:
            issues.append(f"case-colliding archive paths: {previous!r} and {collision_name!r}")
        if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", normalized):
            issues.append(f"{name!r}: absolute archive path")
        parts = normalized.rstrip("/").split("/")
        if ".." in parts:
            issues.append(f"{name!r}: archive path traversal")
        if "\\" in name or any(part in {"", "."} for part in parts) or any(":" in part for part in parts):
            issues.append(f"{name!r}: non-canonical archive path")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            issues.append(f"{name!r}: archive symlink")
    return issues


def zip_issues(
    archive_path: Path,
    expected_files: Iterable[str] | None = None,
    *,
    allow_private_env: bool = False,
) -> list[str]:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
    issues = _zip_member_issues(infos)
    files = [PurePosixPath(info.filename.replace("\\", "/")) for info in infos if not info.is_dir()]
    if not files:
        issues.append("archive is empty")
        return sorted(issues)
    roots = {path.parts[0] for path in files if path.parts}
    if roots != {PROJECT_FOLDER}:
        issues.append(f"top-level folders are {sorted(roots)!r}, expected only {PROJECT_FOLDER!r}")
    duplicate_prefix = (PROJECT_FOLDER, PROJECT_FOLDER)
    inventory: set[str] = set()
    for path in files:
        if path.parts[:2] == duplicate_prefix:
            issues.append(f"{path.as_posix()}: nested duplicate project folder")
        relative = PurePosixPath(*path.parts[1:]).as_posix() if len(path.parts) > 1 else path.as_posix()
        inventory.add(relative)
        reason = path_issue(relative, allow_private_env=allow_private_env)
        if reason:
            issues.append(f"{path.as_posix()}: {reason}")
    issues.extend(_inventory_issues(inventory, expected_files))
    issues.extend(documentation_issues(inventory))
    return sorted(issues)


def extract_zip_safely(
    archive_path: Path,
    destination: Path,
    expected_files: Iterable[str] | None = None,
    *,
    allow_private_env: bool = False,
) -> Path:
    issues = zip_issues(archive_path, expected_files, allow_private_env=allow_private_env)
    if issues:
        raise ValueError("Unsafe release ZIP: " + "; ".join(issues))
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"Extraction destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            relative = Path(*PurePosixPath(info.filename).parts)
            target = (destination / relative).resolve()
            try:
                target.relative_to(resolved_destination)
            except ValueError as error:
                raise ValueError(f"Archive member escapes extraction root: {info.filename}") from error
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    return destination / PROJECT_FOLDER


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--allowlisted-source",
        action="store_true",
        help="check only the explicit source allowlist and required release documents",
    )
    args = parser.parse_args()
    path = args.path.resolve()
    if args.allowlisted_source:
        issues = release_input_issues(path)
    else:
        issues = zip_issues(path) if path.suffix.lower() == ".zip" else tree_issues(path)
    if issues:
        print("Release hygiene failed:")
        for issue in issues:
            print(f"- {issue}")
        return 1
    print(f"Release hygiene passed: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
