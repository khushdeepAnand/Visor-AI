Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$secretTool = Join-Path $root "scripts\manage_secrets.py"

try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Setup is required. Run SETUP_STOCKPILOT.bat first."
    }
    Write-Host "Preferred read-only market-data credential" -ForegroundColor Cyan
    Write-Host "Replacement values are encrypted with Windows DPAPI and are not displayed." -ForegroundColor Cyan
    & $venvPython $secretTool rotate upstox
    if ($LASTEXITCODE -ne 0) {
        throw "The encrypted Upstox configuration was not updated."
    }
    Write-Host "Run VERIFY_UPSTOX_LIVE.bat for the read-only diagnostic." -ForegroundColor Cyan
    exit 0
}
catch {
    Write-Host "CONFIGURATION FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
