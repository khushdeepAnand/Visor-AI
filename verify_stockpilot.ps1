param(
    [switch]$SkipAudit,
    [switch]$SkipBrowserInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [string]$WorkingDirectory = $root
    )

    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$FailureMessage (exit code $LASTEXITCODE)."
        }
    }
    finally {
        Pop-Location
    }
}

try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Setup is required. Run SETUP_STOCKPILOT.bat first."
    }
    & $venvPython -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw ".venv must use Python 3.12. Remove .venv and run SETUP_STOCKPILOT.bat again."
    }

    $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
    }
    if ($null -eq $npmCommand) {
        throw "npm was not found. Install a Node.js version allowed by frontend/package.json."
    }

    Write-Host "[1/14] Validating release inputs, required documents, and secret hygiene..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @("scripts/verify_release_inputs.py") -FailureMessage "Release input verification failed"

    Write-Host "[2/14] Running focused security and export regression gates..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @(
        "-m", "pytest", "-q", "-p", "no:randomly", "tests/test_model_runtime.py", "tests/test_reports.py", "tests/test_release_security.py"
    ) -FailureMessage "Focused security and export regression gates failed"

    Write-Host "[3/14] Validating Windows launchers and static readiness contracts..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @("-m", "pytest", "-q", "-p", "no:randomly", "tests/test_windows_launchers.py") -FailureMessage "Launcher validation failed"

    Write-Host "[4/14] Running backend tests..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @("-m", "pytest", "-q", "-W", "error", "-p", "no:randomly", "--ignore=tests/test_windows_launchers.py") -FailureMessage "Backend tests or warning regression gate failed"

    Write-Host "[5/14] Running backend tests in randomized order..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @(
        "-m", "pytest", "-q", "-W", "error", "--ignore=tests/test_windows_launchers.py", "--randomly-seed=20260831"
    ) -FailureMessage "Randomized backend tests failed"

    Write-Host "[6/14] Compiling Python sources..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @(
        "-m", "compileall", "-q", "-x", "(^|[\\/])(\.venv|node_modules|\.next|\.git)([\\/]|$)", $root
    ) -FailureMessage "Python compilation failed"

    Write-Host "[7/14] Running mypy..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @("-m", "mypy", "--config-file", "mypy.ini", ".") -FailureMessage "mypy failed"

    if (-not $SkipAudit) {
        Write-Host "[8/14] Auditing the installed Python environment..." -ForegroundColor Cyan
        Invoke-Checked -Executable $venvPython -Arguments @("-m", "pip_audit", "--local") -FailureMessage "pip-audit failed"
    }
    else {
        Write-Host "[8/14] Python audit skipped by request." -ForegroundColor Yellow
    }

    Write-Host "[9/14] Restoring frontend dependencies from package-lock.json..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("ci", "--no-fund", "--no-audit") -FailureMessage "npm ci failed" -WorkingDirectory $frontend

    if (-not $SkipBrowserInstall) {
        Write-Host "Installing Playwright Chromium for verification..." -ForegroundColor Cyan
        Invoke-Checked -Executable $npmCommand.Source -Arguments @("exec", "--", "playwright", "install", "chromium") -FailureMessage "Playwright Chromium installation failed" -WorkingDirectory $frontend
    }

    Write-Host "[10/14] Running frontend tests..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("run", "test") -FailureMessage "Frontend tests failed" -WorkingDirectory $frontend

    Write-Host "[11/14] Running frontend type checking..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("run", "typecheck") -FailureMessage "Frontend type checking failed" -WorkingDirectory $frontend

    Write-Host "[12/14] Building the frontend..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("run", "build") -FailureMessage "Frontend build failed" -WorkingDirectory $frontend

    if (-not $SkipAudit) {
        Write-Host "[13/14] Auditing production frontend dependencies..." -ForegroundColor Cyan
        Invoke-Checked -Executable $npmCommand.Source -Arguments @("audit", "--omit=dev", "--audit-level=high") -FailureMessage "Frontend audit failed" -WorkingDirectory $frontend
    }
    else {
        Write-Host "[13/14] Frontend audit skipped by request." -ForegroundColor Yellow
    }

    Write-Host "[14/14] Running Windows Playwright end-to-end tests..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("run", "test:e2e") -FailureMessage "Playwright end-to-end tests failed" -WorkingDirectory $frontend

    Write-Host "StockPilot verification completed successfully." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "VERIFICATION FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
