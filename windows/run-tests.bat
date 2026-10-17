@echo off
rem Runs the full test suite and the parser eval gate inside the Syena image.
cd /d "%~dp0\.."
docker run --rm -e SYENA_ENV=test -v "%cd%\backend\tests:/app/backend/tests:ro" --entrypoint sh syena-backend:latest -c "pip install --user -q pytest hypothesis >/dev/null && python -m pytest -q -p no:cacheprovider tests && syena eval-gate"
pause
