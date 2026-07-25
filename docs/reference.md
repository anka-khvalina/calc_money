# Справочник API и маппингов

Единый источник для Supabase REST, History API и соответствия полей UI ↔ БД.

Конфигурация:

| Файл | Назначение |
|------|------------|
| `config/supabase.json` | desktop: `rest_url`, `anon_key` |
| `web/supabase.config.json` | web/iOS |
| `web/api.config.json` | `api_base_url` (History API, default `http://localhost:8765`) |

Переменные окружения (desktop teams): `SUPABASE_REST_URL`, `SUPABASE_ANON_KEY`.

**Запрещено на клиенте:** `service_role`, пароль БД, secret key.

---

## Авторизация Supabase

Все запросы:

```http
apikey: <SUPABASE_ANON_KEY>
Authorization: Bearer <SUPABASE_ANON_KEY>
Content-Type: application/json
Accept: application/json
```

Базовый URL: `https://<project>.supabase.co/rest/v1`

---

## Supabase REST

### Лиги

```http
GET /leagues?select=id,name&order=name.asc
```

| Поле | Использование |
|------|---------------|
| `id` | UUID лиги, `selected_league_id` |
| `name` | подпись в UI |

### Команды

```http
GET /team?select=id,leagues_id,name_team&leagues_id=eq.{league_id}&order=id.asc
POST /team?select=id,leagues_id,name_team
```

Тело POST: `{"leagues_id": "...", "name_team": "..."}`

### Сводка сезонов

```http
GET /v_season_summary?select=league_id,league_name,season_id,season_label,matches_count,imported_at&matches_count=gt.0&order=league_name.asc,season_label.desc
```

Фильтр по лиге (вкладка «Линия»):

```http
GET /v_season_summary?select=...&league_id=eq.{league_id}&matches_count=gt.0&order=season_label.desc
```

### Матчи (чтение)

`v_matches_full` — все матчи (и `active=true`, и `active=false`).  
**История:** без фильтра по `active`. **Обучение (Линия):** `active=eq.true` и без явного `motivation=false` (`null`/unset остаётся в обучении).

```http
GET /v_matches_full?select={fields}&league_id=eq.{league_id}&season_id=eq.{season_id}&order=match_date.asc
```

Несколько сезонов (обучение):

```http
GET /v_matches_full?select={fields}&league_id=eq.{league_id}&season_id=in.(3,4)&active=eq.true&order=match_date.asc
```

**Поля `select` (канонический список):**

```text
match_id,match_date,league_id,league_name,season_id,season_label,
home_team_id,home_team,away_team_id,away_team,
closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,
over_odds,under_odds,home_odds,draw_odds,away_odds,
is_neutral,match_weight,derby_weight,neutral_weight,motivation,active,note
```

### Эталон для сравнения (inactive, только UI)

При «Рассчитать линию» — опциональное сравнение с closing-линией из view `v_inactive_matches_full` (не влияет на расчёт):

```http
GET /v_inactive_matches_full?select={fields}&league_id=eq.{league_id}&home_team_id=eq.{home}&away_team_id=eq.{away}&order=match_date.desc&limit=1
```

Если матч не найден — вывод как раньше, без блока сравнения.

**Поля `select`:**

```text
match_id,match_date,season_label,home_team,away_team,
closing_ah_home,closing_total_line,ah_home_odds,ah_away_odds,
over_odds,under_odds,home_odds,draw_odds,away_odds
```

### Матчи (сохранение)

```http
PATCH /matches?id=eq.{match_id}
Prefer: return=representation
```

Только поля из **PATCH whitelist** (см. ниже).  
`home_team`, `away_team`, `match_date` и др. read-only — из view `v_matches_full`.

---

## History API (userbet)

Запуск: `bash scripts/run_history_api.sh` → `http://127.0.0.1:8765`

| Метод | Путь | Тело | Ответ |
|-------|------|------|-------|
| GET | `/health` | — | `{"ok": true}` |
| POST | `/api/history/fetch-odds` | `{"id_fixture": "1611253099"}` | `{"odds": {...}}` |

Поля `odds`: `home_odds`, `draw_odds`, `away_odds`, `closing_total_line`, `over_odds`, `under_odds`, `closing_ah_home`, `ah_home_odds`, `ah_away_odds`.

CORS: `HISTORY_API_CORS` (default `*`).

---

## Маппинг UI «История» ↔ БД

Порядок столбцов линии: **AH1 → AH → AH2 → O → Тот → U → 1 → X → 2**

| UI | Ключ кода | Поле БД | PATCH |
|----|-----------|---------|-------|
| AH1 | `ah1` | `ah_home_odds` | да |
| AH | `ah` | `closing_ah_home` | да |
| AH2 | `ah2` | `ah_away_odds` | да |
| O | `over` | `over_odds` | да |
| Тот | `tot` | `closing_total_line` | да |
| U | `under` | `under_odds` | да |
| 1 | `o1` | `home_odds` | да |
| X | `ox` | `draw_odds` | да |
| 2 | `o2` | `away_odds` | да |
| Нейтральное поле | `neutral` | `is_neutral` | да (в блоке **▼ Веса**) |
| Дерби | `derby` | `derby_weight` | да (флаг в **▼ Веса**; влияет на **H**, не на вес) |
| Вес матча | `match_w` | `match_weight` | да |
| Нейтр. вес | `neutr_w` | `neutral_weight` | да (только если нейтральное поле = да) |
| Ротация хозяев | `home_rot` | `home_rotation_code` | да (▼ Веса; влияет на **training_weight** при обучении) |
| Ротация гостей | `away_rot` | `away_rotation_code` | да (▼ Веса; влияет на **training_weight** при обучении) |
| Источник | `source` | `note` | да (в блоке **▼ Веса**; default `Pinnacle`) |
| Мотивация | `motivation` | `motivation` | да (▼ Веса; `false`/«нет» → вне обучения; на Отчёте серый с кэфами, без MAE; `null` = «да») |
| Активен | `active` | `active` | да (в блоке **▼ Веса**; `true` → в обучении, `false` → только история) |

**Сохранение:** кнопка «Сохранить» в строке — после **ручной** правки ячейки или весов.
| Дата | — | `match_date` | нет |
| Дома / Гости | — | `home_team` / `away_team` | нет |

Константы в коде: `app/supabase_history.py` — `UI_COL_TO_FIELD`, `PATCH_WHITELIST`, `HIST_LINE_UI_COLS`, `HIST_WEIGHT_UI_COLS`.

### PATCH whitelist

```text
ah_home_odds, closing_ah_home, ah_away_odds,
over_odds, closing_total_line, under_odds,
home_odds, draw_odds, away_odds,
is_neutral, match_weight, derby_weight, neutral_weight,
home_rotation_code, away_rotation_code,
note, motivation, active
```

Справочник уровней ротации: таблица `match_rotation_levels` (коды `none` / `middle` / `high`).
Чтение: `v_matches_full.home_rotation_code` / `away_rotation_code`.
Запись: PATCH в `matches` — только код; на UI — `name_ru` из справочника.

**Обучение:** из кодов считается match-level `training_weight` (конфиг `rotationTrainingWeights`):

| max(home, away) | weight |
| --- | ---: |
| `high` | `0.0` (исключён) |
| `middle` | `0.7` |
| `none` | `1.0` |

Вес умножается на `wBase` до разделения Legacy/Auto. В отчётах: `training_weight`, `training_status` (`full`/`reduced`/`excluded`), `rotation_reason`.

### Валидация PATCH

- Коэффициенты (`ah_*`, `over_odds`, `under_odds`, `home_odds`, `draw_odds`, `away_odds`): **> 1**
- `match_weight`, `neutral_weight`: **≥ 0**
- `derby_weight`: **1** (дерби) или **0** / `null` (не дерби); поправка H — при обучении
- `is_neutral`: boolean
- `home_rotation_code`, `away_rotation_code`: `none` | `middle` | `high` (пустое → `none`)
- `motivation`: boolean (`да` / `нет` на UI)
- `active`: boolean (`да` / `нет` на UI)

---

## Маппинг «Линия» — DB → модель

Команды и лиги в модели хранятся **по id** (`home_team_id`/`away_team_id`/`league_id`). Имена идут в параллельные поля для отображения (`id — name`). CSV без `*_team_id` использует имя как ключ (legacy).

| Поле `v_matches_full` | Внутреннее (JS / обучение) |
|-----------------------|----------------------------|
| `match_date` | `date` |
| `home_team_id` | `homeId` → ключ `home` (рейтинги) |
| `away_team_id` | `awayId` → ключ `away` (рейтинги) |
| `league_id` | `leagueId` |
| `home_team` | `homeName` (отображение) |
| `away_team` | `awayName` (отображение) |
| `closing_ah_home` | `ah` |
| `closing_total_line` | `tot` |
| `ah_home_odds` | `aho` |
| `ah_away_odds` | `aha` |
| `over_odds` | `ovr` |
| `under_odds` | `und` |
| `home_odds` | `ho` |
| `draw_odds` | `dо` (ox) |
| `away_odds` | `ao` |
| `is_neutral` | `neu` (i_home = 0) |
| `match_weight` | `mw` |
| `derby_weight` | `der` | флаг: **1** = дерби, **0** = нет; H_derby при обучении |
| `neutral_weight` | `neuw` |
| `season_id` | `seasonId` → `season_weight` |

### Обязательные поля для обучения

```text
home_team, away_team, closing_ah_home, closing_total_line,
ah_home_odds, ah_away_odds, over_odds, under_odds
```

Желательно: `home_odds`, `draw_odds`, `away_odds` (для калибровки 1X2 / сравнения с эталоном).

### Legacy CSV (CLI / desktop)

| CSV-колонка | Поле БД / модели |
|-------------|------------------|
| `home_team_id`, `away_team_id` | ключи команд (если есть; иначе имя) |
| `league_id` | id лиги |
| `value`, `quality_weight` | `match_weight` |
| `neutral_flag` | `is_neutral` |
| `derby_flag` | факт дерби (legacy CSV); в UI — колонка «Дерби» |

Экспорт `matches_to_goal_csv` теперь пишет `*_team_id` и `league_id`, чтобы ключи модели переживали round-trip CSV.

---

## Итоговый вес матча

```text
final_match_weight = season_weight × match_weight × neutral_mult

neutral_mult = is_neutral ? neutral_weight : 1.0
```

Дерби **не** входит в вес матча. Флаг дерби используется при обучении для оценки поправки к домашнему преимуществу `H`:

```text
D = r_home - r_away + H_eff

H_eff = H_league                         # обычный матч
H_eff = shrink(H_league + δ_derby)       # дерби (δ_derby из регрессии по истории)
H_eff = 0                                # нейтральное поле
```

| Параметр | Где задаётся |
|----------|--------------|
| `season_weight` | вкладка «Линия», таблица весов по `season_id` |
| `match_weight` | БД, вкладка «История» |
| Дерби (да/нет) | БД, блок **▼ Веса** — влияет на **H** при прогнозе |
| `neutral_weight` | БД, блок **▼ Веса** (только если нейтральное поле = да) |

Defaults весов сезонов (если не меняли): новый → **1**, предыдущий → **0.7**, старше → **0.5**.

---

## userbet → UI «История»

| Поле API | UI-колонка |
|----------|------------|
| `ah_home_odds` | AH1 |
| `closing_ah_home` | AH |
| `ah_away_odds` | AH2 |
| `over_odds` | O |
| `closing_total_line` | Тот |
| `under_odds` | U |
| `home_odds` | 1 |
| `draw_odds` | X |
| `away_odds` | 2 |

Код: `app/userbet_odds.py` → `odds_to_ui_edits`.
