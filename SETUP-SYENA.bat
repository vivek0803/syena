@echo off
title Syena setup
cd /d "%~dp0"
set COMPOSE=docker compose -f deploy\docker-compose.local.yml --env-file deploy\syena-local.conf
set LOG=windows\setup-log.txt
echo Syena setup started %DATE% %TIME% > %LOG%

echo ============================================================
echo  STEP 1 of 5 - Checking Docker Desktop
echo ============================================================
:waitdocker
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running. Opening Docker Desktop... waiting for it to start.
  echo docker not running >> %LOG%
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  timeout /t 15 /nobreak >nul
  goto waitdocker
)
echo Docker is running.
echo docker ok >> %LOG%

echo.
echo ============================================================
echo  STEP 2 of 5 - Saving the laptop setup to GitHub
echo ============================================================
git config user.email >nul 2>&1 || git config user.email "kurisettivivek@gmail.com"
git config user.name >nul 2>&1 || git config user.name "Vivek"
git remote get-url origin >nul 2>&1 || git remote add origin https://github.com/vivek0803/syena.git
git add .gitignore backend deploy windows SETUP-SYENA.bat
git diff --cached --quiet || git commit -q -m "Laptop setup: Telegram polling, local compose, Windows scripts"
git push -u origin main >> %LOG% 2>&1 && (echo Pushed to GitHub. & echo push ok >> %LOG%) || (echo GitHub push skipped - not needed to run Syena. & echo push failed >> %LOG%)

echo.
echo ============================================================
echo  STEP 3 of 5 - Your keys
echo ============================================================
echo Notepad will open your settings file. Paste after the = signs:
echo    SYENA_KITE_API_KEY        (developers.kite.trade - your app)
echo    SYENA_KITE_API_SECRET     (same page)
echo    SYENA_TELEGRAM_BOT_TOKEN  (from @BotFather)
echo    SYENA_ANTHROPIC_API_KEY   (optional - leave empty for now if you like)
echo Do not change the long values that are already filled in.
echo SAVE (Ctrl+S) and CLOSE Notepad to continue.
findstr /r /c:"^SYENA_KITE_API_KEY=." deploy\syena-local.conf >nul && (echo Keys already filled in - skipping Notepad.) || notepad deploy\syena-local.conf
findstr /r /c:"^SYENA_KITE_API_KEY=." deploy\syena-local.conf >nul && echo kite key set >> %LOG% || (echo NOTE: Kite key is empty - Syena will start, but Connect Zerodha will not work yet. & echo kite key empty >> %LOG%)
findstr /r /c:"^SYENA_TELEGRAM_BOT_TOKEN=." deploy\syena-local.conf >nul && echo telegram set >> %LOG% || (echo NOTE: Telegram token is empty - no Telegram alerts yet. & echo telegram empty >> %LOG%)

echo.
echo ============================================================
echo  STEP 4 of 5 - Building and starting Syena (first time 10-20 min)
echo ============================================================
powershell -NoProfile -Command "$ErrorActionPreference='Continue'; & cmd /c '%COMPOSE% --progress plain build 2>&1' | Tee-Object -FilePath '%LOG%' -Append; exit $LASTEXITCODE"
if errorlevel 1 goto fail
if not exist windows\.db-ready (
  echo First run: creating a fresh empty database...
  %COMPOSE% down -v >> %LOG% 2>&1
)
%COMPOSE% up -d db >> %LOG% 2>&1 || goto fail
echo Preparing the database...
set TRIES=0
:waitdb
timeout /t 3 /nobreak >nul
set /a TRIES+=1
%COMPOSE% exec -T db pg_isready -U syena -d syena >nul 2>&1
if errorlevel 1 (if %TRIES% LSS 40 goto waitdb)
%COMPOSE% run --rm api alembic upgrade head >> %LOG% 2>&1 || goto fail
echo ok> windows\.db-ready
%COMPOSE% up -d >> %LOG% 2>&1 || goto fail
echo started ok >> %LOG%
echo Syena is running at http://127.0.0.1:8080  (PAPER mode)

echo.
echo ============================================================
echo  STEP 5 of 5 - Create your login
echo ============================================================
echo Choose a username and a password (12+ characters), then scan the
echo QR code with Google Authenticator and type the 6-digit code.
echo Keep your phone ready.
pause
%COMPOSE% run --rm api syena create-admin
if errorlevel 1 (echo login creation failed >> %LOG%) else (echo login created >> %LOG%)
start "" http://127.0.0.1:8080
echo.
echo All done. Sign in at http://127.0.0.1:8080 and click Connect Zerodha.
echo setup finished >> %LOG%
pause
exit /b 0

:fail
echo SETUP FAILED >> %LOG%
echo.
echo Something failed. Tell Claude "setup failed" - Claude can read the log.
pause
exit /b 1
