@echo off
rem One time: creates your Syena username, password and authenticator (2FA) code.
cd /d "%~dp0\.."
docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf run --rm api syena create-admin
pause
