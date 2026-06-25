# Требования к вкладке «Справочник» (Supabase)

Вкладка **«Справочник»** загружает лиги и команды из Supabase вместо захардкоженного списка.

## Таблицы

- `leagues` — список лиг
- `team` — команды (фильтр `team.leagues_id = leagues.id`)

Вьюха `v_matches_full` для справочника **не** используется.

## Авторизация (MVP)

Конфигурация (не в исходном коде):

- Desktop: `config/supabase.json`
- iOS/web: `web/supabase.config.json`
- Переопределение env: `SUPABASE_REST_URL`, `SUPABASE_ANON_KEY`

Заголовки всех REST-запросов:

```http
apikey: <SUPABASE_ANON_KEY>
Authorization: Bearer <SUPABASE_ANON_KEY>
Content-Type: application/json
Accept: application/json
```

Для `POST`, `PATCH`, `DELETE` дополнительно:

```http
Prefer: return=representation
```

Запрещено в клиенте: `service_role key`, database password, secret key.

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

- Desktop: `app/supabase_teams.py`, `app/supabase_config.py`, вкладка в `app/fair_odds_calc.py`
- iOS/web: `sbInitTeamsTab` + `web/supabase.config.json` в `web/FairOddsCalc_iOS.html`
- Шаблоны: `config/supabase.example.json`, `web/supabase.config.example.json`
