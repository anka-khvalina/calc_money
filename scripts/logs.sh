#!/usr/bin/env bash
# Просмотр логов FairOddsCalc (серверы из update_and_serve.sh).
#
#   bash scripts/logs.sh           # статус + последние 50 строк
#   bash scripts/logs.sh -f        # следить в реальном времени (как tail -f)
#   bash scripts/logs.sh --api     # только History API (userbet)
#   bash scripts/logs.sh --web     # только веб + прокси :8080
#   bash scripts/logs.sh -f --api  # онлайн только API
#
# Логи nohup:  .run/fair-odds-api.log  .run/fair-odds-web.log
# С tmux:      tmux attach -t fair-odds-api  |  tmux attach -t fair-odds-web

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LINES="${LOG_LINES:-50}"
FOLLOW=0
WHICH="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--follow) FOLLOW=1; shift ;;
    --api) WHICH="api"; shift ;;
    --web) WHICH="web"; shift ;;
    -n|--lines)
      LINES="${2:-50}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $1 (см. --help)" >&2
      exit 1
      ;;
  esac
done

api_log="$RUN_DIR/fair-odds-api.log"
web_log="$RUN_DIR/fair-odds-web.log"

status_one() {
  local name="$1"
  local pf="$RUN_DIR/${name}.pid"
  if [[ -f "$pf" ]]; then
    local pid
    pid="$(cat "$pf")"
    if kill -0 "$pid" 2>/dev/null; then
      echo "  $name: работает (pid $pid)"
      return 0
    fi
    echo "  $name: pid $pid не активен (устаревший .pid)"
    return 1
  fi
  if command -v tmux >/dev/null 2>&1 && tmux has-session -t "$name" 2>/dev/null; then
    echo "  $name: tmux-сессия «$name» (attach: tmux attach -t $name)"
    return 0
  fi
  echo "  $name: не запущен"
  return 1
}

echo "=== FairOddsCalc — статус ==="
status_one "fair-odds-api" || true
status_one "fair-odds-web" || true
echo ""

show_log() {
  local title="$1"
  local path="$2"
  echo "──────── $title ────────"
  if [[ ! -f "$path" ]]; then
    echo "  (файл нет: $path)"
    echo "  Запуск: bash scripts/update_and_serve.sh --start"
    echo ""
    return
  fi
  if [[ "$FOLLOW" -eq 1 ]]; then
    echo "  tail -f $path"
    echo ""
    tail -n "$LINES" -f "$path"
  else
    tail -n "$LINES" "$path"
    echo ""
  fi
}

if [[ "$FOLLOW" -eq 1 && "$WHICH" == "all" ]]; then
  echo "Онлайн-логи (Ctrl+C — выход):"
  echo ""
  if [[ -f "$api_log" && -f "$web_log" ]]; then
    tail -n "$LINES" -f "$api_log" "$web_log"
  elif [[ -f "$api_log" ]]; then
    tail -f "$api_log"
  elif [[ -f "$web_log" ]]; then
    tail -f "$web_log"
  else
    echo "Нет логов в $RUN_DIR"
    echo "Запустите: bash scripts/update_and_serve.sh --start"
    exit 1
  fi
  exit 0
fi

case "$WHICH" in
  api) show_log "History API (userbet) — $api_log" "$api_log" ;;
  web) show_log "Web + прокси :8080 — $web_log" "$web_log" ;;
  all)
    show_log "History API (userbet) — $api_log" "$api_log"
    show_log "Web + прокси :8080 — $web_log" "$web_log"
    ;;
esac

if [[ "$FOLLOW" -eq 0 ]]; then
  echo "Следить онлайн: bash scripts/logs.sh -f"
  echo "Только API:     bash scripts/logs.sh -f --api"
fi
