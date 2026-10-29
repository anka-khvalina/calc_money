-- Optional store for walk-forward out-of-sample Line predictions.
-- Used by Rotation Adjustment so history is not re-fit on every click.
-- Run in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS historical_oos_predictions (
  id bigserial PRIMARY KEY,
  match_id text,
  league_id uuid,
  season_id bigint,
  home_team_id bigint,
  away_team_id bigint,
  match_date date NOT NULL,
  train_cutoff date NOT NULL,          -- exclusive: model trained on date < train_cutoff
  prediction_as_of date NOT NULL,      -- usually = match_date
  model_version text NOT NULL DEFAULT 'legacy_line_walkforward_v1',
  estimator_note text DEFAULT 'walkforward_oos',
  p1 double precision NOT NULL,
  px double precision NOT NULL,
  p2 double precision NOT NULL,
  k1 double precision,
  kx double precision,
  k2 double precision,
  train_n integer,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (league_id, season_id, home_team_id, away_team_id, match_date, model_version)
);

CREATE INDEX IF NOT EXISTS historical_oos_predictions_league_date_idx
  ON historical_oos_predictions (league_id, match_date);

ALTER TABLE historical_oos_predictions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_historical_oos_predictions" ON historical_oos_predictions;
CREATE POLICY "anon_select_historical_oos_predictions"
  ON historical_oos_predictions FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "anon_insert_historical_oos_predictions" ON historical_oos_predictions;
CREATE POLICY "anon_insert_historical_oos_predictions"
  ON historical_oos_predictions FOR INSERT TO anon WITH CHECK (true);

COMMENT ON TABLE historical_oos_predictions IS
  'Walk-forward OOS 1X2 from Line Legacy math; closing must not be an input to these probs.';
