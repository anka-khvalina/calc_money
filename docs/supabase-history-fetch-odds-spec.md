# Получение коэффициентов с внешнего сайта (вкладка «История»)

## Цель

Автоматическое заполнение/обновление коэффициентов матча из userbet.info с предпросмотром в строке и сохранением через PATCH в `matches`.

Обновляются только поля:

```text
ah_home_odds, closing_ah_home, ah_away_odds,
over_odds, closing_total_line, under_odds,
home_odds, draw_odds, away_odds
```

## UI

В колонке **«Действие»** (iOS) / колонке **«Данные»** (desktop) — действие **«Получить данные»**.

Раскрывающийся блок под строкой:

```text
id матча с сайта неизвестного мужика: [__________]   [Получить данные]
```

После получения значения подставляются в строку → строка dirty → зелёная **«Сохранить»** → PATCH.

## Внешний API

```http
POST https://userbet.info/user/get_current_lineups_odds/
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
Accept: application/json, text/html, */*

id_fixture={external_match_id}
```

**Не JSON.** Тело — form-urlencoded, как на сайте userbet.info.

### iOS web и CORS

Браузер блокирует прямой запрос с htmlpreview/GitHub Pages. Решение — Supabase Edge Function:

```bash
supabase functions deploy userbet-odds
```

iOS сначала вызывает `{project}/functions/v1/userbet-odds`, затем fallback на прямой POST (desktop).

## Разбор ответа

Массив объектов `{m, b, h, t, l, v}`.

Группы по `m` (3 уникальных значения):

| Порядок `m` | Рынок           |
| ----------- | ----------------- |
| минимальный | 1X2               |
| средний     | Total             |
| максимальный| Asian Handicap    |

MVP: если несколько букмекеров `b`, использовать `b = 70` при наличии.

### 1X2

`l = "1"` → `home_odds`, `l = "x"` → `draw_odds`, `l = "2"` → `away_odds`

### Total

Для каждой линии `t` — пара `o`/`u`. Выбрать линию с минимальным `abs(over - under)`.

### Asian Handicap

Для каждой линии `h` — пара `l=1` / `l=2`. Выбрать линию с минимальным `abs(ah1 - ah2)`.

Нормализация: `"+0"` → `0`, невалидные `h` (например `" 5-1"`) пропускаются. `v` округляется до 2 знаков.

## Ошибки

| Ситуация              | Сообщение                                                    |
| --------------------- | ------------------------------------------------------------ |
| Пустой external id    | Введите id матча с сайта неизвестного мужика                 |
| HTTP сбой             | Не удалось получить данные с внешнего сайта                  |
| Пустой ответ          | Внешний сайт не вернул коэффициенты по матчу                |
| ≠ 3 группы `m`        | Не удалось определить группы рынков из ответа внешнего сайта |
| Нет 1X2               | Не удалось определить коэффициенты 1X2                       |
| Нет тотала            | Не удалось определить основной тотал                         |
| Нет форы              | Не удалось определить основную фору                          |
| PATCH сбой            | Не удалось сохранить коэффициенты в БД                       |

## Реализация

- `app/userbet_odds.py` — fetch + parse + `odds_to_ui_edits`
- Desktop: `app/fair_odds_calc.py`
- iOS: `userbetParseOdds` + `sbHistFetchOdds` в `web/FairOddsCalc_iOS.html`

## Открытые вопросы

1. ~~Точный body POST~~ — подтверждено: `application/x-www-form-urlencoded`, поле `id_fixture`.
2. Правило выбора букмекера при нескольких `b` (MVP: `b = 70`).
