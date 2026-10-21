@echo off
rem Exports Syena's database from this laptop so it can be loaded on your server (deploy/import-laptop.sh).
rem The file contains your trades, decisions and login, so keep it private and delete it after the move.
cd /d "%~dp0\.."
set OUT=windows\syena-laptop-export.sql.gz
docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf up -d db
docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf exec -T db sh -c "pg_dump -U syena --no-owner syena | gzip" > %OUT%
if errorlevel 1 (
  echo Export failed.
) else (
  echo.
  echo Saved %OUT%
  echo Copy it to the server, e.g.:  scp %OUT% root@YOUR-SERVER-IP:/opt/syena/
  echo Then on the server:  cd /opt/syena ^&^& ./deploy/import-laptop.sh syena-laptop-export.sql.gz
)
pause
