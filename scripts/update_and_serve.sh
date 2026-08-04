#!/usr/bin/env bash
# FairOddsCalc — обновление с GitHub и настройка LAN (Mac → iPhone / другой ПК в Wi‑Fi).
#
# Использование:
#   bash scripts/update_and_serve.sh              # pull + api.config.json
#   bash scripts/update_and_serve.sh --start      # + запуск API и веб-прокси
#   bash scripts/update_and_serve.sh --no-pull    # только конфиг
#
# Переменные:
#   FAIR_ODDS_BRANCH   ветка для git pull (default: cursor/bulk-match-weight-17b5)
#   FAIR_ODDS_WEB_PORT порт веб+прокси (default: 8080)
#   HISTORY_API_PORT   порт History API на localhost (default: 8765)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUN_DIR="$ROOT/.run"

BRANCH="${FAIR_ODDS_BRANCH:-cursor/bulk-match-weight-17b5}"
WEB_PORT="${FAIR_ODDS_WEB_PORT:-8080}"
API_PORT="${HISTORY_API_PORT:-8765}"
DO_PULL=1
DO_START=0

for arg in "$@"; do
  case "$arg" in
    --start) DO_START=1 ;;
    --no-pull) DO_PULL=0 ;;
    -h|--help)
      sed -n '2,13p' "$0"
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $arg (см. --help)" >&2
      exit 1
      ;;
  esac
done

detect_lan_ip() {
  local ip=""
  if [[ "$(uname -s)" == "Darwin" ]]; then
    for iface in en0 en1 en2; do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [[ -n "$ip" && "$ip" != 127.0.0.1 ]] && break
    done
  fi
  if [[ -z "$ip" ]]; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
  fi
  if [[ -z "$ip" ]]; then
    echo "Не удалось определить IP в локальной сети." >&2
    exit 1
  fi
  echo "$ip"
}

write_api_config() {
  local ip="$1"
  mkdir -p "$ROOT/web"
  printf '%s\n' '{"api_base_url": ""}' > "$ROOT/web/api.config.json"
  echo "http://${ip}:${WEB_PORT} (API через /api/ на том же порту)"
}

check_health() {
  local url="$1"
  command -v curl >/dev/null 2>&1 && curl -fsS --max-time 5 "$url" >/dev/null 2>&1
}

tmux_cmd() {
  if [[ -f /exec-daemon/tmux.portal.conf ]]; then
    tmux -f /exec-daemon/tmux.portal.conf "$@"
  else
    tmux "$@"
  fi
}

start_tmux_session() {
  local name="$1"
  local workdir="$2"
  shift 2
  if tmux_cmd has-session -t "=$name" 2>/dev/null; then
    echo "  tmux «${name}» уже запущена"
    return 0
  fi
  tmux_cmd new-session -d -s "$name" -c "$workdir" -- "${SHELL:-bash}" -lc "$*"
  echo "  tmux «${name}» запущена"
}

start_background() {
  local name="$1"
  shift
  local pidfile="$RUN_DIR/${name}.pid"
  local logfile="$RUN_DIR/${name}.log"
  mkdir -p "$RUN_DIR"
  if [[ -f "$pidfile" ]]; then
    local oldpid
    oldpid="$(cat "$pidfile")"
    if kill -0 "$oldpid" 2>/dev/null; then
      echo "  ${name} уже работает (pid ${oldpid})"
      return 0
    fi
  fi
  nohup "$@" >>"$logfile" 2>&1 &
  echo $! >"$pidfile"
  echo "  ${name} запущен (pid $(cat "$pidfile"), лог ${logfile})"
}

start_servers() {
  if command -v tmux >/dev/null 2>&1; then
    echo ">> Запуск через tmux..."
    start_tmux_session "fair-odds-api" "$ROOT" \
      "HISTORY_API_HOST=127.0.0.1 bash scripts/run_history_api.sh"
    start_tmux_session "fair-odds-web" "$ROOT" \
      "python3 scripts/serve_lan.py --port ${WEB_PORT}"
    echo "  Остановить: tmux kill-session -t fair-odds-api; tmux kill-session -t fair-odds-web"
    echo "  Логи tmux:  tmux attach -t fair-odds-api   |   bash scripts/logs.sh (файлы .run/)"
  else
    echo ">> tmux не найден — запуск в фоне (nohup, каталог .run/)..."
    start_background "fair-odds-api" env HISTORY_API_HOST=127.0.0.1 bash scripts/run_history_api.sh
    echo "  ждём History API..."
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      check_health "http://127.0.0.1:${API_PORT}/health" && break
      sleep 1
    done
    start_background "fair-odds-web" python3 scripts/serve_lan.py --port "${WEB_PORT}"
    echo "  Остановить: bash scripts/stop_servers.sh"
    echo "  Логи:       bash scripts/logs.sh -f"
  fi
}

verify_servers() {
  local lan_ip="$1"
  sleep 2
  if check_health "http://127.0.0.1:${API_PORT}/health"; then
    echo "  History API (localhost:${API_PORT}): OK"
  else
    echo "  History API: FAIL — смотрите лог (см. выше)" >&2
  fi
  if check_health "http://${lan_ip}:${WEB_PORT}/health"; then
    echo "  Прокси LAN (:${WEB_PORT}/health): OK"
  else
    echo "  Прокси LAN: FAIL — запущен ли serve_lan.py?" >&2
  fi
}

echo "=== FairOddsCalc: обновление ==="
echo "Каталог: $ROOT"
echo ""

if [[ "$DO_PULL" -eq 1 ]]; then
  echo ">> git fetch origin ${BRANCH}"
  git fetch origin "$BRANCH"
  echo ">> git checkout ${BRANCH}"
  git checkout "$BRANCH"
  echo ">> git pull origin ${BRANCH}"
  git pull origin "$BRANCH"
  echo ""
fi

LAN_IP="$(detect_lan_ip)"
API_URL="$(write_api_config "$LAN_IP")"
WEB_URL="http://${LAN_IP}:${WEB_PORT}/FairOddsCalc_iOS.html"

echo ">> web/api.config.json → API через порт ${WEB_PORT}"
echo "   ${API_URL}"
echo ""

if [[ ! -f "$ROOT/web/supabase.config.json" ]]; then
  echo "ВНИМАНИЕ: нет web/supabase.config.json" >&2
fi

echo "=== Ссылка для мужа (тот же Wi‑Fi) ==="
echo "  ${WEB_URL}"
echo ""
echo "Проверка: curl http://${LAN_IP}:${WEB_PORT}/health"
echo ""

if [[ "$DO_START" -eq 1 ]]; then
  start_servers
  verify_servers "$LAN_IP"
else
  echo "=== Запуск вручную (два окна Terminal) ==="
  echo "  1) HISTORY_API_HOST=127.0.0.1 bash scripts/run_history_api.sh"
  echo "  2) python3 scripts/serve_lan.py --port ${WEB_PORT}"
  echo ""
  echo "Или: bash scripts/update_and_serve.sh --start"
fi

echo ""
echo "Готово."
