@echo off
setlocal
rem ExecutionPolicy applies only to this PowerShell process.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_stockpilot.ps1" %*
set "exit_code=%errorlevel%"
endlocal & exit /b %exit_code%
