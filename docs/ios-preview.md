# iOS / Safari — как открыть FairOddsCalc

Репозиторий **приватный**. Ссылки `htmlpreview` и `raw.githubusercontent.com` **не работают**.

## GitHub Pages (рекомендуется)

### Шаг 1 — включить Pages (один раз)

1. Откройте: https://github.com/anka-khvalina/calc_money/settings/pages  
2. **Build and deployment → Source:** `Deploy from a branch`  
3. **Branch:** `gh-pages` → папка `/ (root)` → **Save**  
4. Подождите 1–2 минуты.

### Шаг 2 — открыть в Safari

```
https://anka-khvalina.github.io/calc_money/FairOddsCalc_iOS.html
```

Корень (редирект на iOS):

```
https://anka-khvalina.github.io/calc_money/
```

> У private-репо сайт виден **только залогиненным** пользователям с доступом к репозиторию.  
> Для публичной ссылки без логина: Settings → Pages → **Visibility: Public** (нужен план GitHub с публичными Pages для private repo) или сделайте репозиторий Public.

## Локально (всегда работает)

```bash
cd web
python3 -m http.server 8080
```

Safari: http://localhost:8080/FairOddsCalc_iOS.html

Нужен файл `web/supabase.config.json` (anon key) — он лежит в репозитории.

## Mac + другой ПК / телефон в одной Wi‑Fi (LAN)

Ошибка **«Не удалось связаться с API»** на втором устройстве почти всегда из‑за `localhost` в `web/api.config.json` — для гостя `localhost` это его собственный компьютер, не Mac с сервером.

**На Mac (хост):**

```bash
cd ~/calc_money
git pull origin cursor/lan-update-script-17b5
bash scripts/update_and_serve.sh --start
```

Скрипт запускает:
1. History API на `127.0.0.1:8765` (только на Mac)
2. **`scripts/serve_lan.py`** на порту 8080 — статика + прокси `/api/*` и `/health`

Гости открывают **тот же порт 8080** — отдельно открывать 8765 в файрволе не нужно.

**Важно:** не используйте `python3 -m http.server` для доступа с другого ПК — API не проксируется.

**На втором устройстве:** `http://192.168.1.195:8080/FairOddsCalc_iOS.html`

Проверка с ПК мужа (PowerShell):

```powershell
curl http://192.168.1.195:8080/health
```

Должно вернуть `{"ok":true}`.

Диагностика на Mac:

```bash
bash scripts/check_lan_api.sh
```

Только обновить конфиг без автозапуска:

```bash
bash scripts/update_and_serve.sh
```

Ветка по умолчанию: `cursor/goal-line-supabase-17b5` (переопределение: `FAIR_ODDS_BRANCH=main bash scripts/update_and_serve.sh`).

## Деплой

При push в `web/` на ветках `main`, `cursor/supabase-history-17b5` и др. CI обновляет ветку `gh-pages`.
