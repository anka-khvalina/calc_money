# FairOddsCalc — desktop

Python + Tkinter. Полная документация: **[../docs/README.md](../docs/README.md)**.

## Запуск

```bash
python3 app/fair_odds_calc.py
```

Конфиг Supabase: `config/supabase.json` (скопировать из `config/supabase.example.json`).

## Вкладки

| Вкладка | Данные |
|---------|--------|
| Калькулятор A | ручной ввод коэффициентов |
| Рейтинг команд | CSV 1X2 |
| Справочник | Supabase `leagues`, `team` |
| Счёт кэф | Shin по истории |
| История | Supabase `v_season_summary`, `v_matches_full`, PATCH `matches` |
| Линия (голы) | Supabase или CSV (CLI) → голевая модель |

Маппинги и API: [../docs/reference.md](../docs/reference.md).  
Формулы: [../docs/calculation.md](../docs/calculation.md).

## Голевая модель (CLI)

Обучение из CSV (разработка / тесты):

```bash
python3 app/goal_model_train.py train \
  --input docs/examples/closing_lines_serie_a_sample.csv \
  --home Inter --away Empoli

python3 app/goal_model_train.py validate \
  --input docs/examples/closing_lines_serie_a_sample.csv --min-train 12
```

В web-версии вкладка **«Линия»** загружает матчи из Supabase (см. [architecture.md](../docs/architecture.md)).

## History API

```bash
bash scripts/run_history_api.sh
```

См. [../docs/reference.md](../docs/reference.md#history-api-userbet).

## Сборка .exe

```bash
pyinstaller --onefile --windowed --name FairOddsCalc app/fair_odds_calc.py
```

GitHub Actions: workflow `Build Windows EXE`.

## Тесты

```bash
python3 -m pytest tests/ -v
```
