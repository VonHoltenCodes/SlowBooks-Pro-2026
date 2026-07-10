@echo off
rem ============================================================================
rem SlowBooks Pro 2026 — one-click Windows setup bootstrapper.
rem
rem This is the ONLY file you need to download. It:
rem   1. Asks Windows for Administrator rights (a UAC prompt) — needed to
rem      install Python and the PDF-rendering component system-wide.
rem   2. Downloads the current setup script from the SlowBooks Pro GitHub
rem      repository (so fixes and improvements apply without you ever
rem      needing a new copy of this file).
rem   3. Runs it.
rem
rem Note: the first time you run a downloaded script, Windows SmartScreen
rem may show "Windows protected your PC" — click "More info" then
rem "Run anyway". That warning is expected for any unsigned script.
rem ============================================================================
setlocal

rem ---- Self-elevate to Administrator (triggers one UAC prompt) ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting Administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "PS1_URL=https://raw.githubusercontent.com/moshgrossman/SlowBooks-Pro-2026/main/Setup-SlowBooksPro.ps1"
set "PS1_FILE=%TEMP%\Setup-SlowBooksPro.ps1"

echo Downloading the SlowBooks Pro setup script...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%PS1_URL%' -OutFile '%PS1_FILE%'"

if not exist "%PS1_FILE%" (
    echo.
    echo ERROR: Could not download the setup script. Check your internet
    echo connection and run this file again.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1_FILE%"

echo.
pause
