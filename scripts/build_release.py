"""Build the sanitized StockPilot Windows ZIP from the explicit allowlist."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.release_hygiene import PROJECT_FOLDER, collect_allowlisted, extract_zip_safely, release_input_issues, tree_issues, zip_issues
from scripts.scan_secrets import Finding, env_policy_issues, scan_paths


def _release_secret_findings(paths: list[Path], *, allow_env_secrets: bool) -> list[Finding]:
    findings = scan_paths(paths)
    if not allow_env_secrets:
        return findings
    return [
        finding
        for finding in findings
        if not finding.location.replace("\\", "/").split("!")[-1].endswith("/.env")
        and Path(finding.location.split("!")[-1]).name != ".env"
    ]


def _run(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    print(f"Running from extracted release: {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def verify_extracted_release(
    root: Path,
    *,
    backend: bool,
    frontend: bool,
    frontend_audit: bool,
    launchers: bool,
) -> None:
    environment = os.environ.copy()
    environment["STOCKPILOT_EXTRACTED_RELEASE_VERIFICATION"] = "1"
    environment["STOCKPILOT_ENV"] = "test"
    environment["STOCKPILOT_PROVIDER_MODE"] = "OFFLINE_DEMO"
    environment["STOCKPILOT_JWT_SECRET"] = "temporary-extracted-verification-key-20260831"
    environment.pop("PYTHONPATH", None)
    # Mandatory even when expensive full verification flags are skipped: never
    # let a structurally valid but unimportable ZIP reach a user again.
    _run([sys.executable, "scripts/check_dangling_refs.py"], root, environment)
    _run([sys.executable, "-c", "import main; assert main.app is not None"], root, environment)
    _run([sys.executable, "-m", "pytest", "tests/test_app_smoke.py", "-k", "smoke", "-q", "-p", "no:randomly"], root, environment)
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if backend or launchers:
        _run([sys.executable, "-m", "venv", str(root / ".venv")], root, environment)
        _run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "-r", "requirements-dev.lock"],
            root,
            environment,
        )
        _run([str(python), "-m", "pip", "check"], root, environment)
    if launchers:
        _run(
            [str(python), "-m", "pytest", "-q", "-p", "no:randomly", "tests/test_windows_launchers.py"],
            root,
            environment,
        )
    if backend:
        _run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "-W",
                "error",
                "-p",
                "no:randomly",
                "--ignore=tests/test_windows_launchers.py",
            ],
            root,
            environment,
        )
        _run(
            [str(python), "-m", "pytest", "-q", "-W", "error", "--randomly-seed=20260831", "--ignore=tests/test_windows_launchers.py"],
            root,
            environment,
        )
        _run([str(python), "-m", "compileall", "-q", "api", "services", "scripts", "tests"], root, environment)
        _run([str(python), "-m", "mypy", "--config-file", "mypy.ini", "."], root, environment)
        _run([str(python), "-m", "pip_audit", "--local"], root, environment)
    if frontend:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if npm is None:
            raise RuntimeError("npm was not found for extracted frontend verification.")
        frontend_root = root / "frontend"
        for arguments in (
            ["ci", "--no-fund", "--no-audit"],
            ["run", "test"],
            ["run", "typecheck"],
            ["run", "build"],
        ):
            _run([npm, *arguments], frontend_root, environment)
        if frontend_audit:
            _run([npm, "audit", "--omit=dev", "--audit-level=high"], frontend_root, environment)
        _run([npm, "run", "test:e2e"], frontend_root, environment)


def build(
    root: Path,
    output: Path,
    *,
    verify_backend: bool = False,
    verify_frontend: bool = False,
    verify_frontend_audit: bool = False,
    verify_launchers: bool = False,
    allow_env_secrets: bool = False,
    ignore_local_env_policy: bool = False,
) -> tuple[int, int, str]:
    issues = release_input_issues(root)
    if issues:
        raise RuntimeError("Release inputs incomplete: " + "; ".join(issues))
    # Verify required documents exist in source tree and allowlist
    import subprocess
    for gate in ("check_required_docs.py", "check_dangling_refs.py"):
        result = subprocess.run([sys.executable, f"scripts/{gate}"], cwd=root, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Release gate {gate} failed:\n{result.stdout}\n{result.stderr}")

    files = collect_allowlisted(root, include_private_env=allow_env_secrets)
    expected_files = {source.relative_to(root).as_posix() for source in files}
    findings = _release_secret_findings(files, allow_env_secrets=allow_env_secrets)
    if findings:
        raise RuntimeError(f"Allowlisted source secret scan failed with {len(findings)} finding(s).")

    if not allow_env_secrets and not ignore_local_env_policy:
        policy_issues = env_policy_issues(root)
        if policy_issues:
            raise RuntimeError(
                f"Local environment secret policy failed with {len(policy_issues)} finding(s). "
                "Set --allow-env-secrets only when a build is explicitly intended to "
                "package a populated local environment file."
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stockpilot-release-", dir=output.parent) as temporary:
        staged_root = Path(temporary) / PROJECT_FOLDER
        for source in files:
            relative = source.relative_to(root)
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        issues = tree_issues(staged_root, expected_files, allow_private_env=allow_env_secrets)
        if issues:
            raise RuntimeError("Staged release hygiene failed: " + "; ".join(issues))
        staged_findings = _release_secret_findings([staged_root], allow_env_secrets=allow_env_secrets)
        if staged_findings:
            raise RuntimeError(f"Staged release secret scan failed with {len(staged_findings)} finding(s).")

        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source in sorted(staged_root.rglob("*")):
                if source.is_file():
                    archive.write(source, source.relative_to(staged_root.parent).as_posix())

    try:
        issues = zip_issues(output, expected_files, allow_private_env=allow_env_secrets)
        if issues:
            raise RuntimeError("ZIP inventory failed: " + "; ".join(issues))
        findings = _release_secret_findings([output], allow_env_secrets=allow_env_secrets)
        if findings:
            raise RuntimeError(f"ZIP secret scan failed with {len(findings)} finding(s).")

        with tempfile.TemporaryDirectory(prefix="StockPilot release extraction ") as temporary:
            extraction_directory = Path(temporary)
            if " " not in str(extraction_directory):
                raise RuntimeError("Release verification extraction path must contain a space.")
            extracted_root = extract_zip_safely(
                output,
                extraction_directory,
                expected_files,
                allow_private_env=allow_env_secrets,
            )
            issues = tree_issues(extracted_root, expected_files, allow_private_env=allow_env_secrets)
            if issues:
                raise RuntimeError("Extracted release hygiene failed: " + "; ".join(issues))
            findings = _release_secret_findings([extracted_root], allow_env_secrets=allow_env_secrets)
            if findings:
                raise RuntimeError(f"Extracted release secret scan failed with {len(findings)} finding(s).")
            verify_extracted_release(
                extracted_root,
                backend=verify_backend,
                frontend=verify_frontend,
                frontend_audit=verify_frontend_audit,
                launchers=verify_launchers,
            )
    except Exception:
        output.unlink(missing_ok=True)
        raise

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return len(files), output.stat().st_size, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=root)
    parser.add_argument("--output", type=Path, default=root.parent / f"{PROJECT_FOLDER}-WINDOWS.zip")
    parser.add_argument("--skip-backend-verification", action="store_true")
    parser.add_argument("--skip-frontend-verification", action="store_true")
    parser.add_argument("--skip-frontend-audit", action="store_true")
    parser.add_argument("--skip-launcher-verification", action="store_true")
    parser.add_argument(
        "--allow-env-secrets",
        action="store_true",
        help="explicitly allow packaging a populated local environment file (override the secret policy fail-closed gate)",
    )
    parser.add_argument(
        "--ignore-local-env-policy",
        action="store_true",
        help="build a sanitized archive that excludes private environment files even when local files are populated",
    )
    args = parser.parse_args()
    if args.allow_env_secrets and args.ignore_local_env_policy:
        parser.error("--allow-env-secrets and --ignore-local-env-policy are mutually exclusive")
    try:
        count, size, digest = build(
            args.root.resolve(),
            args.output.resolve(),
            verify_backend=not args.skip_backend_verification,
            verify_frontend=not args.skip_frontend_verification,
            verify_frontend_audit=not args.skip_frontend_audit,
            verify_launchers=not args.skip_launcher_verification,
            allow_env_secrets=args.allow_env_secrets,
            ignore_local_env_policy=args.ignore_local_env_policy,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Release build failed: {error}")
        return 1
    print(f"Release build passed: {count} files")
    print(f"ZIP: {args.output.resolve()}")
    print(f"Size: {size} bytes")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
