# Гlossary — термины FairOddsCalc

Краткий словарь. Подробнее: [training-and-calculation.md](training-and-calculation.md), [calculation.md](calculation.md).

---

## Модель и рынки

| Термин | Значение |
|--------|----------|
| **S_m**, **D_m** | Скрытые сумма и разница голов матча из closing-линии |
| **λ_h**, **λ_a** | Ожидаемые голы хозяев/гостей |
| **H**, **H_league** | Домашнее преимущество (обычный матч) |
| **H_eff** | Эффективное H при прогнозе (нейтраль / дерби / shrinkage) |
| **δ_derby** | Поправка к H по дерби-матчам (регрессия силы) |
| **r_team** | Рейтинг силы команды (Σr = 0) |
| **A**, **Df** | Attack / defense (лог-модель голов) |
| **AH** | Азиатская фора (`closing_ah_home`) |
| **DC** | Dixon–Coles (низкие счёты) |
| **Shin de-vig** | Снятие маржи с линии |

---

## Веса и флаги

| Поле | Роль |
|------|------|
| `season_weight` | Множитель сезона на вкладке «Линия» |
| `match_weight` | Ручной множитель качества (бывш. CSV `value`) |
| `derby_weight` | **Флаг** 1/0: дерби → **H**, не `w_base` |
| `is_neutral` | Нейтральное поле → `H_eff = 0`, `I_home = 0` |
| `neutral_weight` | Ослабление веса **только** если `is_neutral = true` |

```text
w_base = season_weight × match_weight × neutral_mult
neutral_mult = is_neutral ? neutral_weight : 1.0
```

---

## Режимы обучения (defaults)

| Параметр | Default (web baseline) |
|----------|------------------------|
| `use_draw_model` | **false** |
| `draw_model_mode` | `residual_dc` (если вкл) |
| `s_calibration_mode` | **off** |
| `use_dixon_coles` | **true** |
| q legacy | 0.95 / 1.05 |
| q residual_dc | 0.98 / 1.03 |

---

## Ключи команд

| Контекст | Ключ |
|----------|------|
| Supabase `team.id` | integer → `str(id)` в модели |
| Legacy CSV без id | имя команды |
| Desktop registry | `{league}:{n}` — local JSON, не Supabase |

Функции: `team_key()` (Python), `gmTeamKey()` (JS).

---

## Supabase

| Объект | Описание |
|--------|----------|
| `v_matches_full` | View: матч + названия |
| `v_season_summary` | Агрегат сезонов |
| `matches` | Таблица для PATCH |
| `PATCH ?id=eq.{match_id}` | `match_id` из view = колонка `id` таблицы |

Подробнее: [reference.md](reference.md), [db-er-closing-lines.md](db-er-closing-lines.md).
