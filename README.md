# calc_money / FairOddsCalc

Расчёт согласованной футбольной линии из **closing-коэффициентов** (форы, тоталы, 1X2) через голевую модель Poisson / Dixon–Coles.

## Приложения

| Клиент | Запуск |
|--------|--------|
| **Desktop** (Tkinter) | `python3 app/fair_odds_calc.py` |
| **Web / iOS** | `cd web && python3 -m http.server 8080` → [FairOddsCalc_iOS.html](web/FairOddsCalc_iOS.html) |
| **History API** | `bash scripts/run_history_api.sh` |
| **LAN (Mac + второй ПК)** | `bash scripts/update_and_serve.sh --start` |

Подробнее: [app/README.md](app/README.md), [docs/ios-preview.md](docs/ios-preview.md), [docs/scripts.md](docs/scripts.md).

## Данные

Матчи и команды — **Supabase** (`v_matches_full`, `leagues`, `team`).  
Конфиг: `config/supabase.json`, `web/supabase.config.json`.

## Документация

Полный пакет: **[docs/README.md](docs/README.md)**

- [Архитектура](docs/architecture.md)
- [API и маппинги](docs/reference.md) — все ручки и поля
- [Формулы](docs/calculation.md)
- **[Обучение и расчёт (подробно)](docs/training-and-calculation.md)**
- [ER-диаграмма БД](docs/db-er-closing-lines.md)

## Тесты

```bash
python3 -m pip install pytest httpx fastapi
python3 -m pytest tests/ -v
```
