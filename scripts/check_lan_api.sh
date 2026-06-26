#!/usr/bin/env bash
# Быстрая проверка History API для LAN (запускать на Mac-хосте).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${HISTORY_API_PORT:-8765}"

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

echo "=== History API LAN check (port ${PORT}) ==="
echo ""
echo "Слушает:"
if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || echo "  (ничего на порту ${PORT})"
else
  echo "  lsof не найден"
fi
echo ""

for url in "http://127.0.0.1:${PORT}/health" "http://localhost:${PORT}/health"; do
  printf "  %-40s " "$url"
  if curl -fsS --max-time 3 "$url" 2>/dev/null; then echo ""; else echo "FAIL"; fi
done

LAN_IP="$(detect_lan_ip)"
if [[ -n "$LAN_IP" ]]; then
  url="http://${LAN_IP}:${PORT}/health"
  printf "  %-40s " "$url"
  if curl -fsS --max-time 3 "$url" 2>/dev/null; then
    echo ""
    echo ""
    echo "OK для LAN. На втором ПК откройте:"
    echo "  http://${LAN_IP}:8080/FairOddsCalc_iOS.html"
    echo "Проверка с Windows (PowerShell):"
    echo "  curl http://${LAN_IP}:${PORT}/health"
  else
    echo "FAIL"
    echo ""
    echo "С Mac localhost работает, но LAN IP — нет → файрвол macOS или API слушает только 127.0.0.1"
    echo "  bash scripts/run_history_api.sh   # host 0.0.0.0"
    echo "  System Settings → Firewall → разрешить Python"
    exit 1
  fi
else
  echo "LAN IP не определён"
  exit 1
fi

if [[ -f "$ROOT/web/api.config.json" ]]; then
  echo ""
  echo "web/api.config.json:"
  cat "$ROOT/web/api.config.json"
fi
