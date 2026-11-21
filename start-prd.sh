#!/usr/bin/env bash
# Slipstream production gateway launcher (Linux/macOS).
# Idempotent first run: venv -> deps -> .env check -> optional web build,
# then runs uvicorn in the foreground (Ctrl+C to stop).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
ENV_FILE="$ROOT/api/.env"

command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found" >&2; exit 1; }

HOST="${SLIPSTREAM_HOST:-127.0.0.1}"   # LAN: SLIPSTREAM_HOST=0.0.0.0 ./start-prd.sh

if [ ! -x "$PY" ]; then
  echo "==> creating venv at .venv"
  python3 -m venv "$VENV"
  "$PIP" install --upgrade pip -q
fi

if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  if [ -f "$ROOT/api/requirements.lock" ]; then
    echo "==> installing api/requirements.lock (pinned, compiled for Linux)"
    "$PIP" install -r "$ROOT/api/requirements.lock"
  else
    echo "==> installing api/requirements.txt"
    "$PIP" install -r "$ROOT/api/requirements.txt"
  fi
fi

# The LiteLLM transport arm is an opt-in dependency (requirements-litellm.txt):
# only installed when this deployment actually sets UPSTREAM_TRANSPORT=litellm.
# Without the package the gateway's startup guard refuses to boot with a hint
# pointing at this file.
if grep -qsE '^[[:space:]]*UPSTREAM_TRANSPORT[[:space:]]*=[[:space:]]*litellm' "$ENV_FILE" \
  && ! "$PY" -c "import litellm" >/dev/null 2>&1; then
  echo "==> UPSTREAM_TRANSPORT=litellm: installing api/requirements-litellm.txt"
  "$PIP" install -r "$ROOT/api/requirements-litellm.txt"
fi

# 配置不由脚本生成：生产配置里有密钥，模板不该被悄悄复制成"可用"的生产配置。
if [ ! -f "$ENV_FILE" ]; then
  cat >&2 <<'EOF'
WARNING: api/.env not found -- starting with built-in defaults.

  Built-in defaults are development values: APP_ENV=local, JWT_SECRET=dev-secret-change-me,
  admin/admin, SQLite at api/data/app.db. Do NOT expose this instance to a network.

  For a real deployment:
    cp api/.env.example api/.env
    # then set at minimum JWT_SECRET, ADMIN_PASSWORD and DATABASE_URL
EOF
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

# --env-file is only passed when the file exists: uvicorn refuses to start on a
# missing path, and a missing .env is a warning here, not a failure.
UVICORN_ARGS=(
  l07_bootstrap.app_factory:create_app --factory --app-dir src
  --host "$HOST" --port 3680 --log-level info --timeout-keep-alive 75
)
if [ -f "$ENV_FILE" ]; then
  UVICORN_ARGS+=(--env-file .env)
fi

exec "$PY" -m uvicorn "${UVICORN_ARGS[@]}"
