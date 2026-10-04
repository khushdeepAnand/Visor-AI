Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$diagnostic = Join-Path $root "scripts\verify_upstox_live.py"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    Write-Host "Missing project virtual environment. Run SETUP_STOCKPILOT.bat first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath $diagnostic -PathType Leaf)) {
    Write-Host "The read-only Upstox diagnostic was not found." -ForegroundColor Red
    exit 1
}

& $venvPython $diagnostic --refresh-instruments @args
exit $LASTEXITCODE
