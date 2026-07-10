@echo off
rem ============================================================================
rem SlowBooks Pro 2026 — emergency stop (safety net only).
rem
rem Normally you stop SlowBooks Pro simply by closing its window. Use this
rem only if the app didn't exit cleanly and something is still holding the
rem port. It kills whatever is listening on the configured port (APP_PORT
rem in .env, default 3001).
rem ============================================================================
cd /d "%~dp0"

set "PORT=3001"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if /i "%%a"=="APP_PORT" set "PORT=%%b"
    )
)

echo Stopping anything listening on port %PORT%...
powershell -NoProfile -Command ^
    "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Write-Host ('Stopping process ' + $_); Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"

echo Done.
pause
