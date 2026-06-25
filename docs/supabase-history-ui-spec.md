# Вкладка «История» — UI, столбцы и веса

Дополнение к [supabase-history-spec.md](supabase-history-spec.md) и [supabase-history-edit-spec.md](supabase-history-edit-spec.md).

## 1. Порядок столбцов в таблице матчей

В таблице **«Матчи выбранного сезона»** столбцы с линией отображаются в порядке:

```text
AH1 → AH → AH2 → O → Тот → U
```

Полный порядок столбцов (основная строка):

```text
Дата
Дома
Гости
AH1
AH
AH2
O
Тот
U
1
X
2
Нейтр
Действие
```

Дополнительные поля весов (раскрываемый блок, см. §6):

```text
Дерби
Вес матча
Нейтр. вес
```

## 2. Маппинг столбцов на поля БД

| UI-столбец | Поле в БД `matches`             | Редактируемое |
| ---------- | ------------------------------- | ------------- |
| Дата       | `match_date`                    | нет           |
| Дома       | `home_team` из `v_matches_full` | нет           |
| Гости      | `away_team` из `v_matches_full` | нет           |
| AH1        | `ah_home_odds`                  | да            |
| AH         | `closing_ah_home`               | да            |
| AH2        | `ah_away_odds`                  | да            |
| O          | `over_odds`                     | да            |
| Тот        | `closing_total_line`            | да            |
| U          | `under_odds`                    | да            |
| 1          | `home_odds`                     | да            |
| X          | `draw_odds`                     | да            |
| 2          | `away_odds`                     | да            |
| Нейтр      | `is_neutral`                    | да            |
| Дерби      | `derby_weight`                  | да            |
| Вес матча  | `match_weight`                  | да            |
| Нейтр. вес | `neutral_weight`                | да            |
| Действие   | кнопка «Сохранить»              | UI only       |

## 3. Переименование «Кач.»

Столбец **«Кач.»** переименован в **«Вес матча»** (`matches.match_weight`).

Поле — ручной множитель веса матча в модели, а не текстовая оценка качества.

## 4. Формула итогового веса матча

```text
final_match_weight = season_weight × match_weight × derby_weight × neutral_weight
```

| Параметр         | Где настраивается     | Что означает                          |
| ---------------- | --------------------- | ------------------------------------- |
| `season_weight`  | вкладка **«Линия»**   | общий вес выбранного сезона в расчёте |
| `match_weight`   | вкладка **«История»** | ручной вес конкретного матча          |
| `derby_weight`   | вкладка **«История»** | поправка на дерби                     |
| `neutral_weight` | вкладка **«История»** | поправка на нейтральное поле          |

На вкладке **«История»** `season_weight` не редактируется.

### Поведение весов

**`match_weight`** (UI: «Вес матча», default `1`):

- `1` — обычный матч
- `0` — матч исключается из расчёта
- `0.5` — половинный вес
- `>1` — усиление в расчёте

**`derby_weight`** (UI: «Дерби», default `1`): числовой множитель.

**`neutral_weight`** (UI: «Нейтр. вес», default `1`): числовой множитель.

**`is_neutral`** (UI: «Нейтр», да/нет): флаг нейтрального поля.  
`is_neutral` — флаг; `neutral_weight` — множитель. Пример: Нейтр = да, Нейтр. вес = 0.8.

## 5. Раскрываемые дополнительные столбцы

Чтобы таблица не перегружалась, поля **Дерби**, **Вес матча**, **Нейтр. вес** скрыты в раскрываемом блоке (кнопка **«Веса»** в строке).

Поля обязательно доступны в UI и сохраняются в БД при изменении.

## 6. PATCH при изменении

`PATCH /matches?id=eq.{match_id}` — только изменённые поля из whitelist.

Пример:

```json
{
  "ah_home_odds": 1.91,
  "closing_ah_home": -0.25,
  "match_weight": 0.8,
  "derby_weight": 1,
  "neutral_weight": 0.7
}
```

### Whitelist

```text
ah_home_odds
closing_ah_home
ah_away_odds
over_odds
closing_total_line
under_odds
home_odds
draw_odds
away_odds
is_neutral
match_weight
derby_weight
neutral_weight
```

Запрещено: `id`, `season_id`, `match_date`, `home_team_id`, `away_team_id`, `created_at`, `updated_at`, `league_id`.

## 7. Критерии приёмки

1. Порядок линии: `AH1 → AH → AH2 → O → Тот → U`.
2. «Кач.» переименовано в «Вес матча».
3. Редактируются `match_weight`, `derby_weight`, `neutral_weight`.
4. Веса доступны в раскрываемом блоке.
5. При изменении — зелёная кнопка «Сохранить».
6. PATCH только изменённых whitelist-полей.
7. Модель: `season_weight × match_weight × derby_weight × neutral_weight`.
