@echo off
REM ===== Slipstream WSL production gateway: API + web UI on 3680 (single origin) =====
REM Runs in THIS window so every log line is visible; close window or Ctrl+C to stop.
REM Requires: WSL distro Ubuntu-26.04 provisioned (see api/README).
wsl -d Ubuntu-26.04 -e bash -c "cd /home/microsoft/slipstream-python/api && exec ../.venv/bin/python -m uvicorn l07_bootstrap.app_factory:create_app --factory --app-dir src --host 127.0.0.1 --port 3680 --log-level info --timeout-keep-alive 75 --env-file .env.prd"
pause
