# Bash-скрипты (Mac / Linux)

Каталог `scripts/` в корне репозитория — запуск серверов, LAN-доступ и обслуживание.

## Быстрый старт (Mac + iPhone / второй ПК в Wi‑Fi)

```bash
cd ~/calc_money
git pull origin main
bash scripts/update_and_serve.sh --start
```

Скрипт:
1. подтянет код (ветка по умолчанию — см. `FAIR_ODDS_BRANCH` в скрипте);
2. запишет `web/api.config.json` с LAN-IP Mac;
3. запустит History API на `127.0.0.1:8765`;
4. запустит `scripts/serve_lan.py` на порту **8080** — статика + прокси `/api/*` и `/health`.

На втором устройстве откройте: `http://<IP-Mac>:8080/FairOddsCalc_iOS.html`

> Не используйте `python3 -m http.server` для доступа с другого ПК — API не проксируется.

## Скрипты

| Скрипт | Назначение |
|--------|------------|
| `update_and_serve.sh` | `git pull`, конфиг LAN, опционально `--start` |
| `run_history_api.sh` | только History API (:8765) |
| `serve_lan.py` | статика :8080 + прокси API (вызывается из `update_and_serve`) |
| `stop_servers.sh` | остановка фоновых процессов |
| `logs.sh` | логи (`.run/fair-odds-api.log`, `.run/fair-odds-web.log`) |
| `check_lan_api.sh` | проверка `/health` по LAN |
| `reset_derby_flags.py` | сброс `derby_weight=0` для всех матчей в Supabase |

## Команды

```bash
# обновить и запустить
bash scripts/update_and_serve.sh --start

# только конфиг api.config.json, без pull
bash scripts/update_and_serve.sh --no-pull

# другая ветка
FAIR_ODDS_BRANCH=cursor/d-ah-weight-policy-17b5 bash scripts/update_and_serve.sh --start

# логи
bash scripts/logs.sh
bash scripts/logs.sh -f          # follow
bash scripts/logs.sh -f --api    # только API

# остановить
bash scripts/stop_servers.sh

# проверка с другого ПК
curl http://192.168.x.x:8080/health

# сброс дерби: все матчи → не дерби (derby_weight=0)
python3 scripts/reset_derby_flags.py --dry-run
python3 scripts/reset_derby_flags.py
```

## Переменные окружения

| Переменная | Default | Смысл |
|------------|---------|--------|
| `FAIR_ODDS_BRANCH` | см. скрипт | ветка для `git pull` |
| `FAIR_ODDS_WEB_PORT` | `8080` | порт веб+прокси |
| `HISTORY_API_PORT` | `8765` | порт History API (localhost) |

## Файлы состояния

| Путь | Содержимое |
|------|------------|
| `.run/fair-odds-api.pid` | PID History API |
| `.run/fair-odds-web.pid` | PID serve_lan |
| `.run/fair-odds-api.log` | лог API |
| `.run/fair-odds-web.log` | лог веб-прокси |

Подробнее про Safari / GitHub Pages: [ios-preview.md](ios-preview.md).
