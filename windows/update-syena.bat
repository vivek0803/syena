@echo off
rem Rebuilds and restarts Syena with the latest code. Your data is kept.
cd /d "%~dp0\.."
set COMPOSE=docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf
:waitdocker
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Opening Docker Desktop... waiting.
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  timeout /t 15 /nobreak >nul
  goto waitdocker
)
echo update started %DATE% %TIME% > windows\update-log.txt
echo Clearing old Docker build files to save disk space...
docker builder prune -f --filter until=168h >> windows\update-log.txt 2>&1
docker image prune -f >> windows\update-log.txt 2>&1
echo Updating Syena (2-5 minutes)...
rem api and worker share one image: build it once, then start everything without rebuilding
%COMPOSE% build api >> windows\update-log.txt 2>&1 || goto fail
set TRY=0
:buildweb
set /a TRY+=1
%COMPOSE% build web >> windows\update-log.txt 2>&1 && goto webok
if %TRY% LSS 3 (echo Download hiccup - retrying in 20 seconds... & timeout /t 20 /nobreak >nul & goto buildweb)
goto fail
:webok
echo Updating the database...
%COMPOSE% up -d --no-build db >> windows\update-log.txt 2>&1 || goto fail
timeout /t 5 /nobreak >nul
%COMPOSE% run --rm --no-deps api alembic upgrade head >> windows\update-log.txt 2>&1 || goto fail
%COMPOSE% up -d --no-build >> windows\update-log.txt 2>&1 || goto fail
docker image prune -f >> windows\update-log.txt 2>&1
echo update ok>> windows\update-log.txt
echo Done. Syena is running at http://127.0.0.1:8080
pause
exit /b 0
:fail
echo update failed>> windows\update-log.txt
echo UPDATE FAILED - tell Claude "update failed".
pause
exit /b 1
