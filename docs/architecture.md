# FairOddsCalc — архитектура

## Продукт

**FairOddsCalc** — расчёт согласованной футбольной линии из closing-коэффициентов.

| Клиент | Файл | Назначение |
|--------|------|------------|
| Desktop | `app/fair_odds_calc.py` | Tkinter, все вкладки |
| Web / iOS | `web/FairOddsCalc_iOS.html` | Safari, вкладки Линия / Справочник / История / Справка |
| CLI | `app/goal_model_train.py` | обучение голевой модели из CSV (разработка) |
| API | `app/history_api.py` | прокси userbet для кнопки «Получить данные» |

Данные матчей и команд — **Supabase** (`config/supabase.json`, `web/supabase.config.json`).  
Только **anon key** на клиенте.

## Поток данных

```text
Supabase (leagues, team, v_season_summary, v_matches_full, matches)
        │
        ├─► Вкладка «История» — просмотр, редактирование кэфов, PATCH matches
        │
        ├─► Вкладка «Линия» — загрузка матчей, обучение модели, прогноз
        │
        └─► Вкладка «Справочник» — лиги и команды

userbet.info ◄── History API ◄── «Получить данные» (вкладка История)
```

Схема БД: [db-er-closing-lines.md](db-er-closing-lines.md).

## Вкладки (web / desktop)

| Вкладка | Источник данных | Действия |
|---------|-----------------|----------|
| **Линия** | `leagues`, `v_season_summary`, `v_matches_full` | загрузка сезонов, обучение, расчёт линии матча |
| **Справочник** | `leagues`, `team` | список команд, логотипы (локально) |
| **История** | `v_season_summary`, `v_matches_full`, `PATCH matches` | сезоны, правка кэфов и весов |
| **Справка** | — | встроенная документация |

Desktop дополнительно: Калькулятор A, Рейтинг 1X2, Счёт кэф (Shin), локальный CSV-импорт истории (legacy).

## Голевая модель (вкладка «Линия»)

```text
closing-линии → de-vig (Shin) → S_m, D_m → λ_h, λ_a → матрица P(i,j)
       → коррекция ничьи → 1X2, тоталы, форы, ИТ, точный счёт
```

Подробные формулы: [calculation.md](calculation.md).

### Вес матча при обучении

```text
w_base = season_weight × match_weight × derby_weight × neutral_weight
```

Далее на этапах WLS:

- сила: `w = w_base × w_line_AH × w_robust`
- attack/defense: `w = w_base × w_line_T × w_robust_λ`

`season_weight` — на вкладке «Линия» (по выбранному `season_id`).  
`match_weight`, `derby_weight`, `neutral_weight` — в БД (`v_matches_full`), редактируются на «Истории».

## Запуск

| Компонент | Команда |
|-----------|---------|
| Desktop | `python3 app/fair_odds_calc.py` |
| Web локально | `cd web && python3 -m http.server 8080 --bind 0.0.0.0` |
| History API | `bash scripts/run_history_api.sh` |
| Тесты | `python3 -m pytest tests/ -v` |

Деплой web: [ios-preview.md](ios-preview.md).

## Код (основные модули)

| Модуль | Роль |
|--------|------|
| `app/goal_model.py` | Poisson-матрица, рынки, Shin de-vig |
| `app/goal_model_train.py` | обучение, CSV, `base_weight` |
| `app/supabase_history.py` | REST истории, PATCH whitelist |
| `app/supabase_teams.py` | REST справочника |
| `app/userbet_odds.py` | парсинг userbet |
| `app/history_api.py` | FastAPI прокси |
| `web/FairOddsCalc_iOS.html` | UI + порт модели в JS |

API и маппинги: [reference.md](reference.md).
