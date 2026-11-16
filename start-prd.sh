#!/usr/bin/env bash
# Slipstream production gateway launcher (Linux/macOS).
# Idempotent first run: venv -> deps -> api/.env.prd -> optional web build,
# then runs uvicorn in the foreground (Ctrl+C to stop).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found" >&2; exit 1; }

if [ ! -x "$PY" ]; then
  echo "==> creating venv at .venv"
  python3 -m venv "$VENV"
  "$PIP" install --upgrade pip -q
fi

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "==> installing api/requirements.txt"
  "$PIP" install -r "$ROOT/api/requirements.txt"
fi

if [ ! -f "$ROOT/api/.env.prd" ]; then
  echo "==> generating api/.env.prd from api/.env.prd.example"
  cp "$ROOT/api/.env.prd.example" "$ROOT/api/.env.prd"
fi

if [ ! -f "$ROOT/web/dist/index.html" ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "==> building web UI into web/dist"
    (cd "$ROOT/web" && npm install --no-audit --no-fund && npm run build)
  else
    echo "NOTE: web/dist missing and npm not found; starting API-only (UI paths will 404)" >&2
  fi
fi

cd "$ROOT/api"
echo "==> gateway on http://127.0.0.1:3680 (foreground; Ctrl+C to stop)"
exec "$PY" -m uvicorn l07_bootstrap.app_factory:create_app --factory --app-dir src \
  --host 127.0.0.1 --port 3680 --log-level info --timeout-keep-alive 75 \
  --env-file .env.prd
