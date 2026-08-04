# EXP Attack/Defence — pre-implementation research

Date: 2026-08-04  
Repo: FairOddsCalc  
Working tree at research time: branch `cursor/bulk-match-weight-17b5`, clean  
Experiment branch target: `cursor/attack-defence-model-17b5` (cloud naming; intent = isolated AD experiment)

## 1. Pipeline (production)

```text
Supabase GET v_matches_full
  → filter active + motivation + full closing line
  → RawMatch / prepare
  → de-vig closing → market S,D → λ_market
  → fit strength (D) + fit_attack_defense on log(λ_market)
  → calibrate / SFA / SFTC / dynamics
  → predict → price markets
  → MAE vs closing (not FT goals)
```

## 2. Entrypoints (do not touch)

- `app/fair_odds_calc.py` (desktop)
- `web/FairOddsCalc_iOS.html` (UI)
- `app/history_api.py` + `scripts/serve_lan.py` / `update_and_serve.sh`
- `app/goal_model_train.py` CLI `train|validate`

## 3. DB

- PostgREST Supabase via `urllib` (`app/supabase_config.py`, `supabase_teams._request`, `supabase_history`)
- Config: `SUPABASE_REST_URL` / `SUPABASE_ANON_KEY` or `config/supabase.json` (existing; not committed secrets by experiment)
- **No SQL transactions / no native READ ONLY session** on REST
- Writes exist: `PATCH /matches`, `POST /team` — experiment must never call them
- **No `home_goals`/`away_score` columns** on `matches`
- **FT scores available in `note`** as `FT h:a FTR …` for ~2741/4496 active rows (La Liga, Serie A, Bundesliga, Ligue 1 historical). PL / unfinished mostly empty notes.

## 4. Safe reuse

- `load_supabase_settings` (read config only)
- Pure math: `goal_model` matrix/markets optional later
- Patterns only — **do not** call `train_full_model`, History PATCH, UI

## 5. Isolation

- New package `experiments/attack_defence/`
- Own GET-only HTTP client (fail-closed on mutating methods / keywords)
- Parse FT from existing `note` (read-only derivation)
- Console entrypoint `python -m experiments.attack_defence.run --dry-run …`
- Production modules must not import experiment

## 6. Files to add (no production edits)

```text
experiments/attack_defence/
  __init__.py
  __main__.py
  run.py
  readonly_db.py
  parse_ft.py
  model.py
  metrics.py
  validate.py
tests/test_attack_defence_experiment.py
```

## 7. Plan

1. Branch from `main`
2. Implement GET-only loader + FT parser + Poisson AD (per league) + fixed holdout
3. Unit tests (math, RO guard, leakage, secrets mask)
4. DB dry-run console
5. Commit / push / PR — production unchanged
