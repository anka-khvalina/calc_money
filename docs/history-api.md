# History API (прокси userbet для web-клиента)

Обход CORS: браузер → **наш бэкенд** → userbet.info.

## Запуск

```bash
pip install -r requirements-api.txt
python3 -m uvicorn history_api:app --app-dir app --host 0.0.0.0 --port 8765
```

Проверка:

```bash
curl -s http://localhost:8765/health
curl -s -X POST http://localhost:8765/api/history/fetch-odds \
  -H 'Content-Type: application/json' \
  -d '{"id_fixture":"1611253099"}'
```

## Endpoint

```http
POST /api/history/fetch-odds
Content-Type: application/json

{
  "id_fixture": "1611253099"
}
```

Ответ:

```json
{
  "odds": {
    "home_odds": 1.56,
    "draw_odds": 4.78,
    "away_odds": 5.58,
    "closing_total_line": 3.5,
    "over_odds": 2.06,
    "under_odds": 1.85,
    "closing_ah_home": -1,
    "ah_home_odds": 1.87,
    "ah_away_odds": 2.06
  }
}
```

`id_fixture` — в **body**, не в URL.

## Конфиг фронта (iOS web)

`web/api.config.json`:

```json
{
  "api_base_url": "http://localhost:8765"
}
```

Для htmlpreview/GitHub Pages укажите **публичный** URL задеплоенного API (localhost с телефона/превью не доступен).

## Поток

```text
iOS → POST /api/history/fetch-odds → userbet.info
     → odds в ответе → кэш строки (edited) → «Сохранить» → PATCH Supabase matches
```

БД на шаге fetch **не используется**.

## CORS

По умолчанию `allow_origins=*`. Переопределение: `HISTORY_API_CORS=https://htmlpreview.github.io,https://your.pages.dev`
