@echo off
rem Starts (or updates) Syena on this computer. Requires Docker Desktop to be running.
cd /d "%~dp0\.."
if not exist deploy\syena-local.conf (
  echo deploy\syena-local.conf is missing. Ask Claude to create it, or copy deploy\syena-local.conf.example.
  pause
  exit /b 1
)
set COMPOSE=docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf
echo Building Syena (the first time takes 10-20 minutes)...
%COMPOSE% build || goto :fail
%COMPOSE% up -d db || goto :fail
echo Updating the database...
%COMPOSE% run --rm api alembic upgrade head || goto :fail
%COMPOSE% up -d || goto :fail
echo.
echo Syena is running at http://127.0.0.1:8080  (mode: PAPER)
start "" http://127.0.0.1:8080
pause
exit /b 0
:fail
echo.
echo Something failed. Copy the messages above and send them to Claude.
pause
exit /b 1
