# Требования к вкладке «Справочник» (Supabase)

Вкладка **«Справочник»** загружает лиги и команды из Supabase вместо захардкоженного списка.

## Таблицы

- `leagues` — список лиг
- `team` — команды (фильтр `team.leagues_id = leagues.id`)

Вьюха `v_matches_full` для справочника **не** используется.

## Авторизация (MVP)

- Base URL: `https://vhoeiyymxghjafyollyg.supabase.co/rest/v1`
- Только **anon key** в клиенте; `service_role` запрещён

## REST-запросы

### Лиги

```http
GET /leagues?select=id,name&order=name.asc
```

### Команды выбранной лиги

```http
GET /team?select=id,leagues_id,name_team&leagues_id=eq.{league_id}&order=id.asc
```

### Добавить команду

```http
POST /team?select=id,leagues_id,name_team
Prefer: return=representation

{"leagues_id": "{uuid}", "name_team": "{name}"}
```

`id` с клиента не передаётся.

## UI

| Элемент | Формат |
|--------|--------|
| Dropdown «Лига» | `leagues.name`; value = `leagues.id` |
| Таблица | `{name_team} (id:{id})`, № с 1 |
| «Команда (ID)» | `{id} — {name_team}`, value = `team.id` |
| Счётчик | `{League name} — N команд` |

## Валидации при добавлении

- Лига не выбрана → «Выберите лигу»
- Пустое имя → «Введите название команды»
- Дубль в лиге → «Такая команда уже есть в этой лиге»
- Перед POST: `trim()` имени

## Ошибки сети

- GET leagues → «Не удалось загрузить список лиг», dropdown disabled
- GET team → «Не удалось загрузить команды выбранной лиги»
- POST team → «Не удалось добавить команду»

## Логотипы

Логика загрузки/удаления логотипов **не меняется** (локальные файлы / localStorage).

## Реализация в репозитории

- Desktop: `app/supabase_teams.py`, вкладка в `app/fair_odds_calc.py`
- iOS/web: блок `sbInitTeamsTab` в `web/FairOddsCalc_iOS.html`
- Переменные окружения (desktop): `SUPABASE_REST_URL`, `SUPABASE_ANON_KEY`
