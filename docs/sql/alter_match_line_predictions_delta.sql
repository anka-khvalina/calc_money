-- match_line_predictions: expected delta range + expected closing range
-- Run in Supabase SQL Editor (anon key cannot ALTER).
--
-- Convention:
--   delta = predicted_odds - closing_odds
--   expected_closing_min = predicted_odds - delta_max
--   expected_closing_max = predicted_odds - delta_min
--
-- Existing corr_*_{min,max} columns store the expected closing range
-- (computed at «Рассчитать линию» and never recomputed later).
-- New delta_*_{min,max} columns store the raw expected delta range.

ALTER TABLE match_line_predictions
  ADD COLUMN IF NOT EXISTS delta_home_min      double precision,
  ADD COLUMN IF NOT EXISTS delta_home_max      double precision,
  ADD COLUMN IF NOT EXISTS delta_draw_min      double precision,
  ADD COLUMN IF NOT EXISTS delta_draw_max      double precision,
  ADD COLUMN IF NOT EXISTS delta_away_min      double precision,
  ADD COLUMN IF NOT EXISTS delta_away_max      double precision,
  ADD COLUMN IF NOT EXISTS delta_ah_home_min   double precision,
  ADD COLUMN IF NOT EXISTS delta_ah_home_max   double precision,
  ADD COLUMN IF NOT EXISTS delta_ah_away_min   double precision,
  ADD COLUMN IF NOT EXISTS delta_ah_away_max   double precision,
  ADD COLUMN IF NOT EXISTS delta_over_min      double precision,
  ADD COLUMN IF NOT EXISTS delta_over_max      double precision,
  ADD COLUMN IF NOT EXISTS delta_under_min     double precision,
  ADD COLUMN IF NOT EXISTS delta_under_max     double precision;

COMMENT ON COLUMN match_line_predictions.delta_home_min IS
  'Expected delta_min for 1 (home): predicted_odds - closing_odds';
COMMENT ON COLUMN match_line_predictions.delta_home_max IS
  'Expected delta_max for 1 (home): predicted_odds - closing_odds';
COMMENT ON COLUMN match_line_predictions.corr_home_odds_min IS
  'Expected closing_min for 1 = pred_home_odds - delta_home_max';
COMMENT ON COLUMN match_line_predictions.corr_home_odds_max IS
  'Expected closing_max for 1 = pred_home_odds - delta_home_min';

-- Same meaning for draw/away/AH/OU corr_* columns:
--   corr_*_min = expected_closing_min
--   corr_*_max = expected_closing_max
