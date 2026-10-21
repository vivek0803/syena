@echo off
rem Saves all your Syena code changes to GitHub, so your server can download them (deploy/deploy.sh does git pull).
rem Your secrets (deploy\syena-local.conf, deploy\.env), logs, backups and exports are excluded by .gitignore.
cd /d "%~dp0\.."
git add -A
git -c user.name="%USERNAME%" commit -m "Syena update %date% %time%"
git push origin HEAD
if errorlevel 1 (
  echo.
  echo Push failed. If Git asks you to sign in, use your GitHub account; then run this again.
) else (
  echo.
  echo Code is on GitHub. On the server run:  cd /opt/syena ^&^& ./deploy/deploy.sh
)
pause
