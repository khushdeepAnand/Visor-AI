@echo off
setlocal
rem ExecutionPolicy applies only to this PowerShell process. Secrets are prompted securely.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_upstox.ps1"
set "exit_code=%errorlevel%"
endlocal & exit /b %exit_code%
