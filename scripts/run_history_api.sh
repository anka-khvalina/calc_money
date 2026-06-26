#!/usr/bin/env bash
# Запуск History API (прокси userbet для iOS web).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv-api"
if [[ ! -d "$VENV" ]]; then
  echo "Создаю виртуальное окружение .venv-api ..."
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "Устанавливаю зависимости..."
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements-api.txt

HOST="${HISTORY_API_HOST:-127.0.0.1}"
PORT="${HISTORY_API_PORT:-8765}"
echo ""
echo "History API: http://${HOST}:${PORT}  (LAN: через прокси на порту 8080, scripts/serve_lan.py)"
echo "Проверка:    curl http://127.0.0.1:${PORT}/health"
echo ""
exec python -m uvicorn history_api:app --app-dir app --host "$HOST" --port "$PORT"
