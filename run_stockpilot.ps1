Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$frontendNext = Join-Path $root "frontend\node_modules\.bin\next.cmd"
$envPath = Join-Path $root ".env"
$setupRequired = -not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
    -not (Test-Path -LiteralPath $frontendNext -PathType Leaf) -or
    -not (Test-Path -LiteralPath $envPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $root "frontend\.next\BUILD_ID") -PathType Leaf)

if (-not $setupRequired) {
    & $venvPython -c "import sys, uvicorn, pytest, mypy; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
    $setupRequired = $LASTEXITCODE -ne 0
}

if (-not $setupRequired) {
    $secretTool = Join-Path $root "scripts\manage_secrets.py"
    & $venvPython $secretTool status --require STOCKPILOT_JWT_SECRET STOCKPILOT_MFA_SECRET *> $null
    $setupRequired = $LASTEXITCODE -ne 0
}

if ($setupRequired) {
    Write-Host "First-run requirements are missing; running setup once..." -ForegroundColor Cyan
    $setupScript = Join-Path $root "setup_stockpilot.ps1"
    $powershell = Join-Path $PSHOME "powershell.exe"
    & $powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $setupScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& (Join-Path $root "start_stockpilot.ps1") @args
exit $LASTEXITCODE
