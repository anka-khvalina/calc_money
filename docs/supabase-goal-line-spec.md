# Вкладка «Линия» — данные из Supabase

## Источник данных

- Лиги: `GET /leagues?select=id,name&order=name.asc`
- Сезоны: `GET /v_season_summary?...&league_id=eq.{id}&matches_count=gt.0`
- Матчи: `GET /v_matches_full?...&season_id=in.({ids})`

CSV на вкладке не используется.

## UI

- Dropdown **Лига** (из `leagues`)
- **Multi-select сезонов** + чекбокс «Все сезоны»
- Таблица **весов сезонов** (default: 1 / 0.7 / 0.5)
- **Загрузить из БД** → матчи в память
- **Обучить модель** → расчёт на загруженных матчах

## Вес матча

```text
final_match_weight = season_weight × match_weight × derby_weight × neutral_weight
```

## Mapper DB → модель

| БД | Внутреннее поле |
|----|-----------------|
| `match_date` | `date` |
| `home_team` / `away_team` | `home` / `away` |
| `is_neutral` | `neu` |
| `match_weight` | `mw` |
| `derby_weight` | `derw` |
| `neutral_weight` | `neuw` |
| `season_id` | `seasonId` |

## Валидация

Обязательны: `home_team`, `away_team`, `closing_ah_home`, `closing_total_line`, `ah_home_odds`, `ah_away_odds`, `over_odds`, `under_odds`.

Матчи с неполной линией исключаются; в `goalMeta` показывается счётчик исключённых.

## Клиент

Только `anon_key` в `supabase.config.json`. `service_role` запрещён.
