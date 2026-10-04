param(
    [ValidateRange(10, 300)][int]$ReadyTimeoutSeconds = 120,
    [switch]$ExitAfterReady
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSCommandPath
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"
$backendProcess = $null
$frontendProcess = $null
$exitCode = 1

function Test-PortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Stop-ChildTree {
    param([Diagnostics.Process]$Process)

    if ($null -eq $Process) {
        return
    }
    $Process.Refresh()
    if ($Process.HasExited) {
        return
    }
    & taskkill.exe /PID $Process.Id /T /F *> $null
}

function Test-HttpReady {
    param([Parameter(Mandatory = $true)][string]$Uri)

    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Assert-ProcessRunning {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $Process.Refresh()
    if ($Process.HasExited) {
        throw "$Name exited before readiness (exit code $($Process.ExitCode))."
    }
}

try {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $frontend "node_modules\.bin\next.cmd") -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $root ".env") -PathType Leaf)) {
        throw "Setup is required. Run SETUP_STOCKPILOT.bat first."
    }

    & $venvPython -c "import sys, uvicorn; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Setup is incomplete or .venv is not Python 3.12. Run SETUP_STOCKPILOT.bat again."
    }

    $secretTool = Join-Path $root "scripts\manage_secrets.py"
    & $venvPython $secretTool status --require STOCKPILOT_JWT_SECRET STOCKPILOT_MFA_SECRET *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Setup is incomplete because the DPAPI secret store is missing required keys. Run SETUP_STOCKPILOT.bat again."
    }

    if (-not (Test-Path -LiteralPath (Join-Path $frontend ".next\BUILD_ID") -PathType Leaf)) {
        throw "Frontend production build is missing. Run SETUP_STOCKPILOT.bat first."
    }

    $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        $npmCommand = Get-Command "npm" -ErrorAction SilentlyContinue
    }
    if ($null -eq $npmCommand) {
        throw "npm was not found. Install a compatible Node.js release, then run START_STOCKPILOT.bat again."
    }

    foreach ($port in @(8000, 3000)) {
        if (-not (Test-PortAvailable -Port $port)) {
            Write-Host "ERROR: Port $port is already in use." -ForegroundColor Red
            Write-Host "Recovery: close the process using port $port. To identify its PID, run: netstat -ano | findstr :$port" -ForegroundColor Yellow
            Write-Host "After closing that process, run START_STOCKPILOT.bat again." -ForegroundColor Yellow
            exit 2
        }
    }

    Write-Host "Starting backend and frontend..." -ForegroundColor Cyan
    $backendProcess = Start-Process -FilePath $venvPython -ArgumentList @(
        "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $root -NoNewWindow -PassThru
    $frontendProcess = Start-Process -FilePath $npmCommand.Source -ArgumentList @(
        "run", "start", "--", "--hostname", "127.0.0.1", "--port", "3000"
    ) -WorkingDirectory $frontend -NoNewWindow -PassThru

    Write-Host "Backend PID: $($backendProcess.Id); frontend PID: $($frontendProcess.Id)" -ForegroundColor DarkGray
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    $backendReady = $false
    $frontendReady = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        Assert-ProcessRunning -Process $backendProcess -Name "Backend"
        Assert-ProcessRunning -Process $frontendProcess -Name "Frontend"
        if (-not $backendReady) {
            $backendReady = Test-HttpReady -Uri "http://127.0.0.1:8000/api/v1/ready"
        }
        if (-not $frontendReady) {
            $frontendReady = Test-HttpReady -Uri "http://127.0.0.1:3000/"
        }
        if ($backendReady -and $frontendReady) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not ($backendReady -and $frontendReady)) {
        throw "Readiness timed out after $ReadyTimeoutSeconds seconds. Check the output above, then run START_STOCKPILOT.bat again."
    }

    Write-Host "StockPilot is ready at http://127.0.0.1:3000" -ForegroundColor Green
    $exitCode = 0
    if (-not $ExitAfterReady) {
        Write-Host "Press Ctrl+C to stop both managed process trees." -ForegroundColor Cyan
        while ($true) {
            $backendProcess.Refresh()
            $frontendProcess.Refresh()
            if ($backendProcess.HasExited) {
                $exitCode = if ($backendProcess.ExitCode -eq 0) { 1 } else { $backendProcess.ExitCode }
                Write-Host "Backend exited unexpectedly (exit code $($backendProcess.ExitCode))." -ForegroundColor Red
                break
            }
            if ($frontendProcess.HasExited) {
                $exitCode = if ($frontendProcess.ExitCode -eq 0) { 1 } else { $frontendProcess.ExitCode }
                Write-Host "Frontend exited unexpectedly (exit code $($frontendProcess.ExitCode))." -ForegroundColor Red
                break
            }
            Start-Sleep -Seconds 1
        }
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    $exitCode = 130
}
catch {
    Write-Host "START FAILED: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    Write-Host "Stopping managed StockPilot processes..." -ForegroundColor Cyan
    Stop-ChildTree -Process $frontendProcess
    Stop-ChildTree -Process $backendProcess
}

exit $exitCode
