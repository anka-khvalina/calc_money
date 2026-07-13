#!/usr/bin/env python3
"""
Веб-сервер для LAN: статика из web/ + прокси History API на том же порту.

Порт 8765 остаётся только на 127.0.0.1 (файрвол Mac не нужен для гостей).
Гости ходят на http://IP:8080/api/... — прокси пересылает на localhost:8765.

Запуск:
  python3 scripts/serve_lan.py
  WEB_PORT=8080 API_PORT=8765 python3 scripts/serve_lan.py
"""

from __future__ import annotations

import argparse
import http.server
import os
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"


class LanHandler(http.server.SimpleHTTPRequestHandler):
    api_port: int = 8765

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    def _proxy_to_api(self) -> None:
        target = f"http://127.0.0.1:{self.api_port}{self.path}"
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else None
        headers = {}
        for key in ("Content-Type", "Accept", "Authorization"):
            if key in self.headers:
                headers[key] = self.headers[key]
        req = urllib.request.Request(target, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                self.send_response(resp.status)
                for hk, hv in resp.headers.items():
                    lk = hk.lower()
                    if lk in ("transfer-encoding", "connection"):
                        continue
                    self.send_header(hk, hv)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self.send_response(exc.code)
            ct = exc.headers.get("Content-Type", "application/json")
            self.send_header("Content-Type", ct)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except TimeoutError as exc:
            msg = '{"detail":"History API timeout (userbet). Wait and retry."}'.encode()
            self.send_response(504)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            sys.stderr.write(f"proxy timeout {self.path}: {exc}\n")
        except OSError as exc:
            msg = '{"detail":"History API not running. On Mac: bash scripts/run_history_api.sh"}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            sys.stderr.write(f"proxy error {self.path}: {exc}\n")
        except urllib.error.URLError as exc:
            msg = '{"detail":"History API not running. On Mac: bash scripts/run_history_api.sh"}'.encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(msg)
            sys.stderr.write(f"proxy error {self.path}: {exc}\n")

    def do_GET(self) -> None:
        if self.path == "/health" or self.path.startswith("/api/"):
            self._proxy_to_api()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy_to_api()
            return
        self.send_error(405, "Method Not Allowed")

    def do_OPTIONS(self) -> None:
        if self.path == "/health" or self.path.startswith("/api/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
            self.end_headers()
            return
        self.send_error(404)


def main() -> None:
    parser = argparse.ArgumentParser(description="FairOddsCalc LAN web + API proxy")
    parser.add_argument("--host", default=os.environ.get("WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8080")))
    parser.add_argument("--api-port", type=int, default=int(os.environ.get("HISTORY_API_PORT", "8765")))
    args = parser.parse_args()

    if not WEB_DIR.is_dir():
        raise SystemExit(f"Нет каталога {WEB_DIR}")

    LanHandler.api_port = args.api_port
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((args.host, args.port), LanHandler) as httpd:
        print(f"LAN web:  http://0.0.0.0:{args.port}/FairOddsCalc_iOS.html")
        print(f"API proxy: /health, /api/* → 127.0.0.1:{args.api_port}")
        print("Остановка: Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
