# Документация FairOddsCalc

Техническая документация актуального приложения (desktop + web/iOS + Supabase).

## Порядок чтения

1. [architecture.md](architecture.md) — что есть сейчас: вкладки, потоки данных, модули
2. [reference.md](reference.md) — **все REST-ручки**, маппинги UI ↔ БД, whitelist PATCH
3. [calculation.md](calculation.md) — формулы голевой модели, веса, Shin
4. [db-er-closing-lines.md](db-er-closing-lines.md) — схема БД closing-линий
5. [glossary.md](glossary.md) — термины
6. [ios-preview.md](ios-preview.md) — как открыть web-версию

## Примеры данных

Каталог [examples/](examples/) — CSV для CLI-тестов и `goal_model_train.py`:

| Файл | Назначение |
|------|------------|
| `closing_lines_serie_a_sample.csv` | обучение голевой модели (CLI) |
| `history_epl_2025_26.csv` | тесты Shin / истории |
| `season_odds_la_liga_2024_25.csv` | пример сезонной таблицы |

Основной источник данных в приложении — **Supabase**, не CSV.

## Код

| Путь | Описание |
|------|----------|
| `app/fair_odds_calc.py` | desktop |
| `web/FairOddsCalc_iOS.html` | web / iOS |
| `app/supabase_history.py` | клиент истории |
| `app/goal_model_train.py` | обучение (Python) |
| `tests/` | автотесты |

Запуск тестов: `python3 -m pytest tests/ -v`
