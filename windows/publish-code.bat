@echo off
rem Saves all your Syena code changes to GitHub, so your server can download them (deploy/deploy.sh does git pull).
rem Your secrets (deploy\syena-local.conf, deploy\.env), logs, backups and exports are excluded by .gitignore.
cd /d "%~dp0\.."
git add -A
git diff --cached --quiet || git -c user.name="%USERNAME%" commit -m "Syena update %date% %time%"
git push origin HEAD
if errorlevel 1 (
  echo.
  echo Push failed. If Git asks you to sign in, use your GitHub account; then run this again.
) else (
  echo.
  echo Code is on GitHub. GitHub now runs the tests (github.com/vivek0803/syena/actions, about 5 minutes^).
  echo If they pass, the server deploys it by itself within a few minutes, or after 15:35 on market days.
  echo Syena's Telegram bot tells you when the server has updated.
)
pause
