@echo off
cd /d "%~dp0\.."
docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf stop
echo Syena stopped. Your data is kept; run start-syena.bat to start again.
pause
