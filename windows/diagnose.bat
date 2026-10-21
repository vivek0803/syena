@echo off
rem Rebuilds Syena with the latest fixes and saves logs for Claude to read.
cd /d "%~dp0\.."
set COMPOSE=docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf
echo Updating Syena (1-3 minutes)...
%COMPOSE% up -d --build > windows\diag-log.txt 2>&1
echo.
echo Now try Connect Zerodha again in the browser.
echo When it fails, come back here and press any key.
pause
%COMPOSE% ps >> windows\diag-log.txt 2>&1
%COMPOSE% logs --tail 300 api web >> windows\diag-log.txt 2>&1
echo Saved. Tell Claude "done".
pause
