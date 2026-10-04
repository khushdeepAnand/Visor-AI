from __future__ import annotations

import zipfile
from pathlib import Path

from scripts.release_hygiene import (
    REQUIRED_DOCUMENTS,
    collect_allowlisted,
    documentation_issues,
    path_issue,
    release_input_issues,
    zip_issues,
)
from scripts.scan_secrets import env_policy_issues, scan_paths, scan_text


def test_secret_scanner_rejects_populated_sensitive_environment_values() -> None:
    findings = scan_text("UPSTOX_API_SECRET=" + "sensitive-value", "fixture.env")
    assert [finding.kind for finding in findings] == ["populated_sensitive_environment_variable"]


def test_secret_scanner_rejects_jwt_and_private_key() -> None:
    jwt = ".".join(["eyJ" + "a" * 13, "b" * 16, "c" * 16])
    private_key = "-----BEGIN " + "PRIVATE KEY-----"
    kinds = {finding.kind for finding in scan_text(f"{jwt}\n{private_key}\n", "fixture.txt")}
    assert kinds == {"jwt", "private_key"}


def test_secret_scanner_accepts_empty_documented_examples() -> None:
    text = "UPSTOX_ANALYTICS_TOKEN=\nSTOCKPILOT_JWT_SECRET=\nNEXT_PUBLIC_API_BASE=http://localhost:8000\n"
    assert scan_text(text, ".env.example") == []


def test_secret_scanner_exempts_only_the_known_synthetic_test_password() -> None:
    assert scan_text('PASSWORD = "StrongPass9!x"', "test_fixture.py") == []
    assert scan_text('PASSWORD = "StrongPass9!x"', "application.py")
    assert scan_text('PASSWORD = "different-value"', "test_fixture.py")


def test_secret_scanner_checks_zip_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "release.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("project/.env", "UPSTOX_ACCESS_TOKEN=" + "not-for-release")
    findings = scan_paths([archive_path])
    assert len(findings) == 1
    assert findings[0].location.endswith("!project/.env")


def test_env_policy_rejects_populated_private_environment_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "UPSTOX_ANALYTICS_TOKEN=not-for-release\nSTOCKPILOT_JWT_SECRET=\n", encoding="utf-8"
    )
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / ".env.local").write_text("NEXT_PUBLIC_API_BASE=http://localhost:8000\n", encoding="utf-8")

    findings = env_policy_issues(tmp_path)

    assert len(findings) == 1
    assert findings[0].location.endswith(".env")
    assert findings[0].kind == "populated_sensitive_environment_variable"


def test_env_policy_accepts_placeholder_only_environment_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "UPSTOX_ANALYTICS_TOKEN=<your-token>\nSTOCKPILOT_JWT_SECRET=\n", encoding="utf-8"
    )
    assert env_policy_issues(tmp_path) == []


def test_current_source_tree_has_no_secret_findings() -> None:
    root = Path(__file__).resolve().parents[1]
    release_files = collect_allowlisted(root)
    assert release_files
    assert scan_paths(release_files) == []


def test_secret_scanner_does_not_skip_runtime_or_generated_directories(tmp_path: Path) -> None:
    for directory in ("cache", "logs", "database", "artifacts", ".next", "__pycache__"):
        target = tmp_path / directory
        target.mkdir()
        (target / "evidence.txt").write_text(
            "SERVICE_API_KEY=" + "local-sensitive-value", encoding="utf-8"
        )

    findings = scan_paths([tmp_path])

    assert len(findings) == 6
    assert all(finding.kind == "populated_sensitive_environment_variable" for finding in findings)


def test_secret_scanner_checks_binary_database_content(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    database.write_bytes(b"SQLite format 3\x00\x00SERVICE_TOKEN=embedded-sensitive-value\n")

    findings = scan_paths([database])

    assert [finding.kind for finding in findings] == ["populated_sensitive_environment_variable"]


def test_release_allowlist_excludes_private_and_historical_files() -> None:
    root = Path(__file__).resolve().parents[1]
    relative = {path.relative_to(root).as_posix() for path in collect_allowlisted(root)}
    assert "README.md" in relative
    assert "api/main.py" in relative
    assert "services/market_data/manager.py" in relative
    assert "frontend/app/page.tsx" in relative
    assert "frontend/components/TerminalShell.tsx" in relative
    assert "frontend/tests/e2e/core.spec.ts" in relative
    assert ".env" not in relative
    assert "frontend/.env.local" not in relative
    assert "CONTINUATION_RESULTS.md" not in relative
    assert REQUIRED_DOCUMENTS.issubset(relative)
    assert not any("node_modules" in path or "__pycache__" in path for path in relative)


def test_private_release_can_opt_in_only_the_root_environment_file(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    private_env = root / ".env"
    selected = collect_allowlisted(root, include_private_env=True)
    if private_env.is_file():
        assert private_env in selected
    assert path_issue(".env", allow_private_env=True) is None
    assert path_issue("frontend/.env.local", allow_private_env=True) == "private environment file"
    assert path_issue("nested/.env", allow_private_env=True) == "private environment file"


def test_release_input_gate_requires_governance_documents() -> None:
    root = Path(__file__).resolve().parents[1]
    assert release_input_issues(root) == []
    assert documentation_issues(REQUIRED_DOCUMENTS - {"ADMIN_OPERATIONS.md"}) == [
        "missing required document: ADMIN_OPERATIONS.md"
    ]


def test_release_hygiene_rejects_forbidden_artifacts() -> None:
    assert path_issue("database/stockpilot.db")
    assert path_issue("frontend/node_modules/package/index.js")
    assert path_issue("models/forecast.joblib")
    assert path_issue("nested/release.zip")
    assert path_issue(".env")
    assert path_issue("frontend/.env.local")
    assert path_issue("api/__pycache__/main.pyc")
    assert path_issue("logs/backend.txt")
    assert path_issue("artifacts/generated.csv")
    assert path_issue("database/stockpilot.db-wal")
    assert path_issue("frontend/.next/cache.bin")
    assert path_issue("frontend/tsconfig.tsbuildinfo")
    assert path_issue(".env.example") is None


def test_release_hygiene_rejects_unsafe_and_case_colliding_zip_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("StockPilot-AI-v6.1-SECURE/../outside.txt", "unsafe")
        archive.writestr("StockPilot-AI-v6.1-SECURE/README.md", "one")
        archive.writestr("StockPilot-AI-v6.1-SECURE/readme.md", "two")
    issues = zip_issues(archive_path)
    assert any("traversal" in issue for issue in issues)
    assert any("case-colliding" in issue for issue in issues)
