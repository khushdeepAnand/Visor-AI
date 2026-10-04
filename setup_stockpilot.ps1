param(
    [switch]$SkipBrowserInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvDirectory = Join-Path $root ".venv"
$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
$frontend = Join-Path $root "frontend"
$envPath = Join-Path $root ".env"
$envExamplePath = Join-Path $root ".env.example"

function Test-Python312 {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    try {
        & $Executable @PrefixArguments -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Find-Python312 {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $launcher -and (Test-Python312 -Executable $launcher.Source -PrefixArguments @("-3.12"))) {
        return [PSCustomObject]@{ Executable = $launcher.Source; PrefixArguments = @("-3.12") }
    }

    foreach ($name in @("python3.12.exe", "python.exe")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $candidate -and (Test-Python312 -Executable $candidate.Source)) {
            return [PSCustomObject]@{ Executable = $candidate.Source; PrefixArguments = @() }
        }
    }

    return $null
}

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

function Ensure-SecureSecrets {
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        if (-not (Test-Path -LiteralPath $envExamplePath -PathType Leaf)) {
            throw ".env.example was not found at '$envExamplePath'."
        }
        Copy-Item -LiteralPath $envExamplePath -Destination $envPath
        Write-Host "Created .env from .env.example." -ForegroundColor Green
    }
    $secretTool = Join-Path $root "scripts\manage_secrets.py"
    Invoke-Checked -Executable $venvPython -Arguments @(
        $secretTool, "bootstrap", "--env-file", $envPath
    ) -FailureMessage "DPAPI secret-store initialization failed"
}

function Ensure-SameOriginFrontendApi {
    $frontendEnvExample = Join-Path $frontend ".env.local.example"
    $frontendEnv = Join-Path $frontend ".env.local"
    if (-not (Test-Path -LiteralPath $frontendEnv)) {
        if (Test-Path -LiteralPath $frontendEnvExample -PathType Leaf) {
            Copy-Item -LiteralPath $frontendEnvExample -Destination $frontendEnv
            Write-Host "Created frontend/.env.local from its example." -ForegroundColor Green
        }
        return
    }

    $text = [IO.File]::ReadAllText($frontendEnv)
    $updated = [regex]::Replace(
        $text,
        "(?m)^NEXT_PUBLIC_API_BASE[\t ]*=[\t ]*https?://(?:localhost|127\.0\.0\.1):8000/?[\t ]*\r?$",
        "NEXT_PUBLIC_API_BASE="
    )
    $updated = [regex]::Replace(
        $updated,
        "(?m)^NEXT_PUBLIC_WS_BASE[\t ]*=[\t ]*wss?://(?:localhost|127\.0\.0\.1):8000/?[\t ]*\r?$",
        "NEXT_PUBLIC_WS_BASE="
    )
    if ($updated -ne $text) {
        [IO.File]::WriteAllText($frontendEnv, $updated, (New-Object Text.UTF8Encoding($false)))
        Write-Host "Updated frontend/.env.local to use same-origin authenticated requests." -ForegroundColor Green
    }
}

try {
    Write-Host "StockPilot AI Windows setup" -ForegroundColor Cyan

    $python312 = Find-Python312
    if ($null -eq $python312) {
        throw @"
Python 3.12 is required but was not found. Install the 64-bit Python 3.12 release from:
https://www.python.org/downloads/windows/
Enable the Python launcher (recommended) or add Python 3.12 to PATH, reopen this window, and run SETUP_STOCKPILOT.bat again.
"@
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $venvDirectory) {
            throw "The existing .venv is incomplete. Remove '$venvDirectory', then run SETUP_STOCKPILOT.bat again."
        }
        Write-Host "Creating the Python 3.12 virtual environment..." -ForegroundColor Cyan
        $venvArguments = @($python312.PrefixArguments) + @("-m", "venv", $venvDirectory)
        Invoke-Checked -Executable $python312.Executable -Arguments $venvArguments -FailureMessage "Virtual environment creation failed"
    }

    if (-not (Test-Python312 -Executable $venvPython)) {
        throw "The existing .venv does not use Python 3.12. Remove '$venvDirectory', then run SETUP_STOCKPILOT.bat again."
    }

    Ensure-SecureSecrets

    $requirements = Join-Path $root "requirements-dev.lock"
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "requirements-dev.lock was not found."
    }
    Write-Host "Installing the declared Python environment with the virtual-environment interpreter..." -ForegroundColor Cyan
    Invoke-Checked -Executable $venvPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
        "--require-virtualenv", "--requirement", $requirements
    ) -FailureMessage "Python dependency installation failed"

    if (-not (Test-Path -LiteralPath (Join-Path $frontend "package-lock.json") -PathType Leaf)) {
        throw "frontend/package-lock.json was not found; npm ci requires the lock file."
    }
    $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
    }
    if ($null -eq $npmCommand) {
        throw "npm was not found. Install a Node.js version allowed by frontend/package.json, reopen this window, and run SETUP_STOCKPILOT.bat again."
    }

    Write-Host "Installing the lock-file frontend environment with npm ci..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("ci", "--no-fund", "--no-audit") -FailureMessage "Frontend dependency installation failed" -WorkingDirectory $frontend

    Ensure-SameOriginFrontendApi

    Write-Host "Building the production frontend..." -ForegroundColor Cyan
    Invoke-Checked -Executable $npmCommand.Source -Arguments @("run", "build") -FailureMessage "Frontend production build failed" -WorkingDirectory $frontend

    if (-not $SkipBrowserInstall) {
        Write-Host "Installing Playwright Chromium for verification..." -ForegroundColor Cyan
        Invoke-Checked -Executable $npmCommand.Source -Arguments @("exec", "--", "playwright", "install", "chromium") -FailureMessage "Playwright Chromium installation failed" -WorkingDirectory $frontend
    }

    Invoke-Checked -Executable $venvPython -Arguments @(
        "-c", "import sys, fastapi, uvicorn; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)"
    ) -FailureMessage "Backend import verification failed"

    Write-Host "Setup completed. Run START_STOCKPILOT.bat to launch StockPilot." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
