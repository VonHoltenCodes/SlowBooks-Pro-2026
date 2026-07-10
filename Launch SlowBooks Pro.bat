@echo off
rem ============================================================================
rem SlowBooks Pro 2026 — daily launcher (the Desktop shortcut points here).
rem No Administrator rights needed: nothing is installed, this only runs
rem what setup already installed. Closing the app window stops the server.
rem ============================================================================
cd /d "%~dp0"

rem Fast no-op when pywebview is already installed; repairs it if missing.
python -c "import webview" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing the desktop window component...
    python -m pip install -r requirements-desktop.txt
)

python desktop_launcher.py
if %errorlevel% neq 0 pause
