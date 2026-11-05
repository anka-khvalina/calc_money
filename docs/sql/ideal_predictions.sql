-- Ideal predictions — recreate under real FairOddsCalc types.
-- Run in Supabase SQL Editor (anon cannot DDL).
--
-- Live types (verified against this project):
--   matches.id              bigint
--   leagues.id / league_id  uuid
--   seasons / season_id     bigint
--   team.id / home|away     bigint
--   v_matches_full.match_id = matches.id
--
-- If you already created the table with VARCHAR ids — drop and recreate.
-- Safe while empty / no production ideal rows yet.

DROP TABLE IF EXISTS ideal_predictions CASCADE;

CREATE TABLE ideal_predictions (
  id                     bigserial PRIMARY KEY,

  match_id               bigint NOT NULL,

  league_id              uuid NOT NULL,
  league_name            text NOT NULL,

  season_id              bigint NOT NULL,
  season_label           text NOT NULL,

  home_team_id           bigint NOT NULL,
  away_team_id           bigint NOT NULL,
  home_team              text NOT NULL,
  away_team              text NOT NULL,
  match_date             date NOT NULL,

  prediction_data        jsonb NOT NULL,
  calculation_settings   jsonb NOT NULL,

  saved_at               timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_ideal_predictions_match UNIQUE (match_id),

  CONSTRAINT fk_ideal_predictions_match
    FOREIGN KEY (match_id) REFERENCES matches(id)
    -- no ON DELETE CASCADE (agree separately if matches can be deleted)
);

CREATE INDEX idx_ideal_predictions_league_id
  ON ideal_predictions (league_id);
CREATE INDEX idx_ideal_predictions_season_id
  ON ideal_predictions (season_id);
CREATE INDEX idx_ideal_predictions_season_label
  ON ideal_predictions (season_label);
CREATE INDEX idx_ideal_predictions_match_date
  ON ideal_predictions (match_date);
CREATE INDEX idx_ideal_predictions_home_team
  ON ideal_predictions (home_team);
CREATE INDEX idx_ideal_predictions_away_team
  ON ideal_predictions (away_team);

ALTER TABLE ideal_predictions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_ideal_predictions" ON ideal_predictions;
CREATE POLICY "anon_select_ideal_predictions"
  ON ideal_predictions FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "anon_insert_ideal_predictions" ON ideal_predictions;
CREATE POLICY "anon_insert_ideal_predictions"
  ON ideal_predictions FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_ideal_predictions" ON ideal_predictions;
CREATE POLICY "anon_update_ideal_predictions"
  ON ideal_predictions FOR UPDATE TO anon USING (true) WITH CHECK (true);

COMMENT ON TABLE ideal_predictions IS
  'User-confirmed ideal Line calc snapshot per match; closing odds stay on matches.';

-- After create: Supabase → Project Settings → API → Reload schema (если PostgREST ещё отдаёт 404).
