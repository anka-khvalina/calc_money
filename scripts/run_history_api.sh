#!/usr/bin/env bash
# Запуск History API (прокси userbet для iOS web).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "Устанавливаю зависимости..."
  "$PY" -m pip install -r requirements-api.txt
fi
HOST="${HISTORY_API_HOST:-0.0.0.0}"
PORT="${HISTORY_API_PORT:-8765}"
echo "History API: http://127.0.0.1:${PORT}"
echo "Проверка: curl http://127.0.0.1:${PORT}/health"
exec "$PY" -m uvicorn history_api:app --app-dir app --host "$HOST" --port "$PORT"
