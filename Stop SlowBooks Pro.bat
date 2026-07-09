@echo off
REM ==========================================================================
REM  Slowbooks Pro 2026 - stop the app (Windows)
REM
REM  Stops the Docker containers. Your data is kept safe in Docker volumes
REM  and will be there next time you launch.
REM ==========================================================================

cd /d "%~dp0"

echo Stopping Slowbooks Pro...
docker compose down
if errorlevel 1 (
    docker-compose down
)

echo Done. Your data is preserved.
pause
