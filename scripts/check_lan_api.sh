#!/usr/bin/env bash
# Проверка LAN: веб + API-прокси на одном порту (8080).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_PORT="${FAIR_ODDS_WEB_PORT:-8080}"
API_PORT="${HISTORY_API_PORT:-8765}"

detect_lan_ip() {
  local ip=""
  if [[ "$(uname -s)" == "Darwin" ]]; then
    for iface in en0 en1 en2; do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [[ -n "$ip" ]] && break
    done
  fi
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  echo "$ip"
}

echo "=== FairOddsCalc LAN check ==="
echo ""
echo "History API (localhost, должен быть запущен):"
printf "  http://127.0.0.1:${API_PORT}/health → "
if curl -fsS --max-time 3 "http://127.0.0.1:${API_PORT}/health" 2>/dev/null; then echo ""; else echo "FAIL (bash scripts/run_history_api.sh)"; fi

echo ""
echo "Веб + прокси (для гостей в Wi‑Fi, scripts/serve_lan.py):"
LAN_IP="$(detect_lan_ip)"
if [[ -z "$LAN_IP" ]]; then echo "LAN IP не найден"; exit 1; fi
url="http://${LAN_IP}:${WEB_PORT}/health"
printf "  %s → " "$url"
if curl -fsS --max-time 3 "$url" 2>/dev/null; then
  echo ""
  echo ""
  echo "OK. На ПК мужа: http://${LAN_IP}:${WEB_PORT}/FairOddsCalc_iOS.html"
  echo "PowerShell: curl http://${LAN_IP}:${WEB_PORT}/health"
else
  echo "FAIL"
  echo ""
  echo "Запустите: python3 scripts/serve_lan.py --port ${WEB_PORT}"
  echo "Не используйте «python3 -m http.server» — без прокси API с другого ПК не работает."
  exit 1
fi

if [[ -f "$ROOT/web/api.config.json" ]]; then
  echo ""
  echo "web/api.config.json:"
  cat "$ROOT/web/api.config.json"
fi
