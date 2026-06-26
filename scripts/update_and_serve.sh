#!/usr/bin/env bash
# FairOddsCalc — обновление с GitHub и настройка LAN (Mac → iPhone / другой ПК в Wi‑Fi).
#
# Использование:
#   bash scripts/update_and_serve.sh              # pull + api.config.json с IP Mac
#   bash scripts/update_and_serve.sh --start      # то же + запуск API и веб-сервера в tmux
#   bash scripts/update_and_serve.sh --no-pull    # только конфиг и подсказки
#
# Переменные:
#   FAIR_ODDS_BRANCH   ветка для git pull (default: cursor/goal-line-supabase-17b5)
#   FAIR_ODDS_WEB_PORT порт статики (default: 8080)
#   HISTORY_API_PORT   порт History API (default: 8765)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BRANCH="${FAIR_ODDS_BRANCH:-cursor/goal-line-supabase-17b5}"
WEB_PORT="${FAIR_ODDS_WEB_PORT:-8080}"
API_PORT="${HISTORY_API_PORT:-8765}"
DO_PULL=1
DO_START=0

for arg in "$@"; do
  case "$arg" in
    --start) DO_START=1 ;;
    --no-pull) DO_PULL=0 ;;
    -h|--help)
      sed -n '2,12p' "$0"
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
    echo "Не удалось определить IP в локальной сети. Задайте вручную:" >&2
    echo "  echo '{\"api_base_url\":\"http://ВАШ_IP:${API_PORT}\"}' > web/api.config.json" >&2
    exit 1
  fi
  echo "$ip"
}

write_api_config() {
  local ip="$1"
  local url="http://${ip}:${API_PORT}"
  mkdir -p "$ROOT/web"
  printf '%s\n' "{\"api_base_url\": \"${url}\"}" > "$ROOT/web/api.config.json"
  echo "$url"
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
    echo "  tmux-сессия «${name}» уже запущена (пропуск)"
    return 0
  fi
  tmux_cmd new-session -d -s "$name" -c "$workdir" -- "${SHELL:-bash}" -lc "$*"
  echo "  запущена tmux-сессия «${name}»"
}

check_health() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1
  else
    return 1
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

echo ">> web/api.config.json → api_base_url = ${API_URL}"
echo "   (кнопка «Получить данные» во вкладке История ходит на этот адрес)"
echo ""

if [[ ! -f "$ROOT/web/supabase.config.json" ]]; then
  echo "ВНИМАНИЕ: нет web/supabase.config.json — скопируйте из примера или спросите ключ." >&2
fi

echo "=== Откройте в Safari / Chrome (тот же Wi‑Fi) ==="
echo "  ${WEB_URL}"
echo ""
echo "На этом Mac (локально):"
echo "  http://127.0.0.1:${WEB_PORT}/FairOddsCalc_iOS.html"
echo ""
echo "Проверка History API:"
echo "  curl ${API_URL}/health"
echo ""

if [[ "$DO_START" -eq 1 ]]; then
  echo ">> Запуск серверов в tmux..."
  start_tmux_session "fair-odds-api" "$ROOT" "bash scripts/run_history_api.sh"
  start_tmux_session "fair-odds-web" "$ROOT/web" "python3 -m http.server ${WEB_PORT} --bind 0.0.0.0"
  sleep 2
  if check_health "http://127.0.0.1:${API_PORT}/health"; then
    echo "  History API (localhost): OK"
  else
    echo "  History API: ещё стартует или ошибка — смотрите: tmux attach -t fair-odds-api" >&2
  fi
  if check_health "http://${LAN_IP}:${API_PORT}/health"; then
    echo "  History API (LAN ${LAN_IP}): OK"
  else
    echo "  ВНИМАНИЕ: с другого ПК API не отвечает на ${LAN_IP}:${API_PORT}" >&2
    echo "  → System Settings → Network → Firewall: разрешите Python / входящие на порт ${API_PORT}" >&2
    echo "  → Убедитесь, что API слушает 0.0.0.0: lsof -nP -iTCP:${API_PORT} -sTCP:LISTEN" >&2
  fi
  echo ""
  echo "Остановить: tmux kill-session -t fair-odds-api; tmux kill-session -t fair-odds-web"
  echo "Логи:       tmux attach -t fair-odds-api   |   tmux attach -t fair-odds-web"
else
  echo "=== Запуск вручную (два терминала на Mac) ==="
  echo "  Терминал 1:  bash scripts/run_history_api.sh"
  echo "  Терминал 2:  cd web && python3 -m http.server ${WEB_PORT} --bind 0.0.0.0"
  echo ""
  echo "Или одной командой с автозапуском:"
  echo "  bash scripts/update_and_serve.sh --start"
fi

echo ""
echo "Готово."
