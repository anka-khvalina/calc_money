# Требования к вкладке «История» (Supabase)

Вкладка **«История»** — экран просмотра сохранённых сезонов и матчей из Supabase. CSV-импорт на вкладке **не используется**.

## Источники данных

| View | Назначение |
|------|------------|
| `v_season_summary` | Список сезонов с матчами |
| `v_matches_full` | Матчи выбранного сезона |

## Авторизация

Конфигурация: `config/supabase.json` (desktop), `web/supabase.config.json` (iOS).

Заголовки всех запросов:

```http
apikey: <SUPABASE_ANON_KEY>
Authorization: Bearer <SUPABASE_ANON_KEY>
Content-Type: application/json
Accept: application/json
```

## REST-запросы

### Список сезонов

```http
GET /v_season_summary?select=league_id,league_name,season_id,season_label,matches_count,imported_at&matches_count=gt.0&order=league_name.asc,season_label.desc
```

### Матчи сезона

```http
GET /v_matches_full?select=...&league_id=eq.{league_id}&season_id=eq.{season_id}&order=match_date.asc
```

## UI

### Сохранённые сезоны

| Лига | Сезон | Матчей | Импорт |
|------|-------|--------|--------|

Пусто: `Пусто — в базе пока нет матчей.`

### Матчи выбранного сезона

Дата, Дома, Гости, AH, AH1, AH2, Тот, O, U, 1, X, 2, Нейтр, Дерби, Кач.

- `null` → `—`
- `is_neutral`: `да` / `нет`

## Ошибки

- Сезоны: `Не удалось загрузить список сезонов.`
- Матчи: `Не удалось загрузить матчи выбранного сезона.`

## Реализация

- `app/supabase_history.py`
- Desktop: вкладка «История» в `app/fair_odds_calc.py`
- iOS: `sbInitHistTab` в `web/FairOddsCalc_iOS.html`
- «Линия» → «Загрузить из истории» читает те же данные и конвертирует в CSV
