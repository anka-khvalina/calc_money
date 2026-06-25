# Редактирование коэффициентов на вкладке «История»

См. также [supabase-history-ui-spec.md](supabase-history-ui-spec.md) (порядок столбцов, веса, раскрываемый блок).

## Чтение

`GET /v_matches_full?league_id=eq.{id}&season_id=eq.{id}`

## Сохранение

`PATCH /matches?id=eq.{match_id}` — только whitelist-поля, только изменённые.

## Редактируемые поля (whitelist)

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

## UI

- Desktop: двойной клик по ячейке → редактирование; колонка «Веса» (⚙) → диалог весов; колонка «Сохранить»
- iOS: input/select в ячейках; кнопка «▼ Веса» раскрывает Дерби / Вес матча / Нейтр. вес; зелёная кнопка «Сохранить» при dirty

## Реализация

- `app/supabase_history.py` — `patch_match`, `build_dirty_patch`, валидации
- Desktop: `app/fair_odds_calc.py` (вкладка «История»)
- iOS: `renderHistMatchesTable` + `sbPatchMatch` в `web/FairOddsCalc_iOS.html`
