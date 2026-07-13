# Документация FairOddsCalc

Техническая документация актуального приложения (desktop + web/iOS + Supabase).

## Порядок чтения

1. [architecture.md](architecture.md) — что есть сейчас: вкладки, потоки данных, модули
2. [reference.md](reference.md) — **все REST-ручки**, маппинги UI ↔ БД, whitelist PATCH
3. [calculation.md](calculation.md) — формулы (краткий справочник)
4. **[training-and-calculation.md](training-and-calculation.md)** — **обучение и расчёт линии (подробно)**
5. [db-er-closing-lines.md](db-er-closing-lines.md) — схема БД closing-линий
6. [glossary.md](glossary.md) — термины
7. [ios-preview.md](ios-preview.md) — как открыть web-версию
8. [scripts.md](scripts.md) — bash-скрипты (LAN, логи, API)

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
