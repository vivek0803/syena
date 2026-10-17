@echo off
rem Shows the last 200 log lines of the background worker and API.
cd /d "%~dp0\.."
docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf logs --tail 200 worker api
pause
