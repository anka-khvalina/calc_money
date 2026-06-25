# Редактирование коэффициентов на вкладке «История»

## Чтение

`GET /v_matches_full?league_id=eq.{id}&season_id=eq.{id}`

## Сохранение

`PATCH /matches?id=eq.{match_id}` — только whitelist-поля, только изменённые.

## Редактируемые поля

`closing_ah_home`, `closing_total_line`, `ah_home_odds`, `ah_away_odds`, `over_odds`, `under_odds`, `home_odds`, `draw_odds`, `away_odds`, `is_neutral`, `derby_weight`, `match_weight`

## UI

- Desktop: двойной клик по ячейке → редактирование; колонка «Сохранить»
- iOS: input/select в ячейках; зелёная кнопка «Сохранить» при dirty

## Реализация

- `app/supabase_history.py` — `patch_match`, `build_dirty_patch`, валидации
- Desktop: `app/fair_odds_calc.py` (вкладка «История»)
- iOS: `renderHistMatchesTable` + `sbPatchMatch` в `web/FairOddsCalc_iOS.html`
