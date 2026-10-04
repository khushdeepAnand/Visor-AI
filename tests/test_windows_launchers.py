from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_BATCH_LAUNCHERS = {
    "RUN_STOCKPILOT.bat",
    "SETUP_STOCKPILOT.bat",
    "START_STOCKPILOT.bat",
    "VERIFY_STOCKPILOT.bat",
    "CONFIGURE_UPSTOX.bat",
    "VERIFY_UPSTOX_LIVE.bat",
}
POWERSHELL_LAUNCHERS = {
    "run_stockpilot.ps1",
    "setup_stockpilot.ps1",
    "start_stockpilot.ps1",
    "verify_stockpilot.ps1",
    "configure_upstox.ps1",
    "verify_upstox_live.ps1",
}


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_required_batch_launcher_names_are_exact() -> None:
    actual = {path.name for path in ROOT.glob("*.bat")}
    assert actual == REQUIRED_BATCH_LAUNCHERS


def test_batch_wrappers_are_root_relative_and_preserve_exit_codes() -> None:
    for name in REQUIRED_BATCH_LAUNCHERS:
        text = read(name)
        lowered = text.lower()
        assert '"%~dp0' in text, name
        assert "-executionpolicy bypass" in lowered, name
        assert "set-executionpolicy" not in lowered, name
        assert "set \"exit_code=%errorlevel%\"" in lowered, name
        assert "endlocal & exit /b %exit_code%" in lowered, name


def test_powershell_launchers_resolve_their_own_root_for_paths_with_spaces() -> None:
    for name in POWERSHELL_LAUNCHERS:
        text = read(name)
        assert "$root = Split-Path -Parent $PSCommandPath" in text, name
        assert "Join-Path $root" in text, name

    for name in REQUIRED_BATCH_LAUNCHERS:
        assert re.search(r'-File\s+"%~dp0[^"\r\n]+\.ps1"', read(name), re.IGNORECASE), name


def test_setup_is_setup_only_and_uses_deterministic_install_commands() -> None:
    setup = read("setup_stockpilot.ps1").lower()
    assert 'get-command "py.exe"' in setup
    assert setup.index('get-command "py.exe"') < setup.index('"python3.12.exe"')
    assert "sys.version_info[:2] == (3, 12)" in setup
    assert 'join-path $venvdirectory "scripts\\python.exe"' in setup
    assert '"--require-virtualenv"' in setup
    assert '@("ci", "--no-fund", "--no-audit")' in setup
    assert "manage_secrets.py" in setup
    assert '"bootstrap", "--env-file"' in setup
    assert "dpapi secret-store initialization failed" in setup
    assert "stockpilot_jwt_secret=$secret" not in setup
    assert "start-process" not in setup
    assert "start_stockpilot.ps1" not in setup
    assert re.search(r"\bnpm\s+install\b", setup) is None


def test_installs_and_browser_downloads_do_not_run_during_normal_start() -> None:
    setup = read("setup_stockpilot.ps1").lower()
    verify = read("verify_stockpilot.ps1").lower()
    assert '"playwright", "install", "chromium"' in setup
    assert '"playwright", "install", "chromium"' in verify

    for name in ("run_stockpilot.ps1", "start_stockpilot.ps1"):
        text = read(name).lower()
        assert "npm ci" not in text, name
        assert re.search(r"\bnpm\s+install\b", text) is None, name
        assert "pip\", \"install" not in text, name
        assert "playwright\", \"install" not in text, name


def test_start_uses_managed_processes_readiness_and_bounded_cleanup() -> None:
    start = read("start_stockpilot.ps1").lower()
    assert '$venvpython = join-path $root ".venv\\scripts\\python.exe"' in start
    assert '"-m", "uvicorn"' in start
    assert "/api/v1/ready" in start
    assert "$readytimeoutseconds" in start
    assert ".hasexited" in start
    assert "taskkill.exe /pid $process.id /t /f" in start
    assert "finally" in start
    assert "port $port is already in use" in start
    assert "netstat -ano | findstr :$port" in start
    assert "$exitafterready" in start
    assert "--reload" not in start


def test_run_performs_first_run_detection_before_start() -> None:
    run = read("run_stockpilot.ps1").lower()
    assert "$setuprequired" in run
    assert 'join-path $root "setup_stockpilot.ps1"' in run
    assert 'join-path $root "start_stockpilot.ps1"' in run
    assert run.index('join-path $root "setup_stockpilot.ps1"') < run.index('join-path $root "start_stockpilot.ps1"')
    assert "manage_secrets.py" in run
    assert "status --require stockpilot_jwt_secret stockpilot_mfa_secret" in run


def test_local_frontend_uses_same_origin_rest_and_host_matched_websockets() -> None:
    frontend_env = read("frontend/.env.local.example")
    assert "NEXT_PUBLIC_API_BASE=\n" in frontend_env
    assert "NEXT_PUBLIC_WS_BASE=\n" in frontend_env

    setup = read("setup_stockpilot.ps1")
    assert "Ensure-SameOriginFrontendApi" in setup
    assert "Updated frontend/.env.local to use same-origin authenticated requests." in setup

    start = read("start_stockpilot.ps1").lower()
    assert "manage_secrets.py" in start
    assert "status --require stockpilot_jwt_secret stockpilot_mfa_secret" in start


def test_upstox_launchers_are_secure_and_read_only_in_wording() -> None:
    configure = read("configure_upstox.ps1")
    lowered = configure.lower()
    assert "[string]$AccessToken" not in configure
    assert "manage_secrets.py" in configure
    assert "rotate upstox" in lowered
    assert "windows dpapi" in lowered
    assert "Set-EnvValue" not in configure
    assert "STOCKPILOT_PROVIDER_ORDER" not in configure

    launcher_text = "\n".join(
        read(name)
        for name in (
            "CONFIGURE_UPSTOX.bat",
            "configure_upstox.ps1",
            "VERIFY_UPSTOX_LIVE.bat",
            "verify_upstox_live.ps1",
        )
    )
    assert re.search(r"\b(?:buy|sell|trade|orders?|position)\b", launcher_text, re.IGNORECASE) is None
    assert 'Join-Path $root "scripts\\verify_upstox_live.py"' in read("verify_upstox_live.ps1")


def test_playwright_backend_uses_cross_platform_project_venv() -> None:
    config = read("frontend/playwright.config.ts")
    assert 'path.join(projectRoot, ".venv", "Scripts", "python.exe")' in config
    assert 'path.join(projectRoot, ".venv", "bin", "python")' in config
    assert "shellQuote(venvPython)" in config
    assert "cwd: projectRoot" in config
    assert re.search(r'command:\s*["\'`]python\s+-m\s+uvicorn', config) is None
