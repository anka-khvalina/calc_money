# FairOddsCalc — архитектура

## Продукт

**FairOddsCalc** — расчёт согласованной футбольной линии из closing-коэффициентов.

| Клиент | Файл | Назначение |
|--------|------|------------|
| Desktop | `app/fair_odds_calc.py` | Tkinter, все вкладки |
| Web / iOS | `web/FairOddsCalc_iOS.html` | Safari, вкладки Линия / Справочник / История / Отчет / Справка |
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
        ├─► Вкладка «Линия» — загрузка матчей, обучение Legacy+Auto, комбинированный прогноз
        │
        ├─► Вкладка «Отчет» — calculated vs closing на inactive и motivation=нет (1X2 Legacy, AH/OU Auto)
        │
        └─► Вкладка «Справочник» — лиги и команды

userbet.info ◄── History API ◄── «Получить данные» (вкладка История)
```

Схема БД: [db-er-closing-lines.md](db-er-closing-lines.md).

## Вкладки (web / desktop)

| Вкладка | Источник данных | Действия |
|---------|-----------------|----------|
| **Линия** | `leagues`, `v_season_summary`, `v_matches_full` | загрузка сезонов, обучение Legacy+Auto, комбинированная линия |
| **Справочник** | `leagues`, `team` | список команд, логотипы (локально) |
| **История** | `v_season_summary`, `v_matches_full`, `PATCH matches` | сезоны, правка кэфов и весов |
| **Отчет** | `v_matches_full` (active+motivated train / inactive ∪ unmotivated eval) | MAE без motivation=нет; серые строки с кэфами |
| **Справка** | — | встроенная документация |

Desktop дополнительно: Калькулятор A, Рейтинг 1X2, Счёт кэф (Shin), локальный CSV-импорт истории (legacy).

## Голевая модель (вкладка «Линия»)

Итоговая линия — **комбинация двух независимых моделей** на одной active-выборке:

```text
closing → de-vig → S/D → общие веса (в т.ч. training_weight)
       ├── Legacy: Poisson + Dixon–Coles → 1X2
       └── Auto:   Marginals (α) + Copula (ρ) → AH, OU
```

α/ρ калибруются автоматически (`model_config.json`). Пользователь не выбирает модель-победителя.  
Вкладка **«Отчет»** сравнивает комбинированную линию с closing (без Legacy-vs-Auto).  
Подробные формулы: [calculation.md](calculation.md), [training-and-calculation.md](training-and-calculation.md).

### Вес матча при обучении

```text
w_base = season_weight × match_weight × neutral_mult
neutral_mult = is_neutral ? neutral_weight : 1.0
```

Дерби **не** умножает вес. Флаг `derby_weight = 1` участвует в регрессии силы как `δ_derby · I_derby_home` (поправка к `H`).

Далее на этапах WLS:

- сила (Goal Difference / D): `w = w_base × w_robust` — **AH Line Weight выключен** (`lineWeight.mode=disabled`)
- attack/defense (Total Goals / S): `w = w_base × w_line_T × w_robust_λ`

`season_weight` — на вкладке «Линия» (по выбранному `season_id`).  
`match_weight`, `is_neutral`, `derby_weight`, `neutral_weight` — в БД (`v_matches_full`), блок **▼ Веса** на «Истории».

## Запуск

| Компонент | Команда |
|-----------|---------|
| Desktop | `python3 app/fair_odds_calc.py` |
| Web локально | `cd web && python3 -m http.server 8080 --bind 0.0.0.0` |
| History API | `bash scripts/run_history_api.sh` |
| **LAN (Mac + телефон/ПК)** | `bash scripts/update_and_serve.sh --start` |
| Тесты | `python3 -m pytest tests/ -v` |

Скрипты `scripts/`: [scripts.md](scripts.md).  
Деплой web: [ios-preview.md](ios-preview.md).

## Код (основные модули)

| Модуль | Роль |
|--------|------|
| `app/goal_model.py` | рынки из матрицы, Shin de-vig, legacy DC/draw helpers |
| `app/goal_matrix_auto.py` | Auto Marginals + Copula, VPP, конфиг, backtest old vs new |
| `app/goal_model_train.py` | обучение, CSV, `base_weight` |
| `app/supabase_history.py` | REST истории, PATCH whitelist |
| `app/supabase_teams.py` | REST справочника |
| `app/userbet_odds.py` | парсинг userbet |
| `app/history_api.py` | FastAPI прокси |
| `web/FairOddsCalc_iOS.html` | UI + порт модели в JS |

API и маппинги: [reference.md](reference.md).
