#!/usr/bin/env bash
# Остановить фоновые серверы, запущенные update_and_serve.sh (без tmux).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.run"

stop_pidfile() {
  local name="$1"
  local pf="$RUN_DIR/${name}.pid"
  if [[ ! -f "$pf" ]]; then
    echo "  $name: не запущен (нет $pf)"
    return 0
  fi
  local pid
  pid="$(cat "$pf")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    echo "  $name: остановлен (pid $pid)"
  else
    echo "  $name: процесс $pid уже не работает"
  fi
  rm -f "$pf"
}

echo "=== Остановка FairOddsCalc серверов ==="
if command -v tmux >/dev/null 2>&1; then
  tmux kill-session -t fair-odds-api 2>/dev/null && echo "  tmux fair-odds-api: остановлен" || true
  tmux kill-session -t fair-odds-web 2>/dev/null && echo "  tmux fair-odds-web: остановлен" || true
fi
stop_pidfile "fair-odds-api"
stop_pidfile "fair-odds-web"
echo "Готово."
