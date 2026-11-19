-- Match odds charts (userbet graph_lineups time series).
-- Run in Supabase SQL Editor (anon cannot DDL).
--
-- Live types:
--   matches.id / match_id  bigint
-- External site id (userbet ps_id / id_fixture) stored as text.
--
-- One row = one chart series:
--   our match + external fixture + bookmaker + market + label

CREATE TABLE IF NOT EXISTS match_odds_charts (
  id                   bigserial PRIMARY KEY,

  match_id             bigint NOT NULL,
  external_match_id    text   NOT NULL,

  bookmaker            text   NOT NULL,
  bookmaker_id         integer,

  market               text   NOT NULL,
  label                text   NOT NULL,

  points               jsonb  NOT NULL DEFAULT '[]'::jsonb,

  source_url           text,
  fetched_at           timestamptz NOT NULL DEFAULT now(),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_match_odds_charts_series
    UNIQUE (match_id, external_match_id, bookmaker, market, label),

  CONSTRAINT fk_match_odds_charts_match
    FOREIGN KEY (match_id) REFERENCES matches(id),

  CONSTRAINT chk_match_odds_charts_points_array
    CHECK (jsonb_typeof(points) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_match_odds_charts_match_id
  ON match_odds_charts (match_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_charts_external_match_id
  ON match_odds_charts (external_match_id);
CREATE INDEX IF NOT EXISTS idx_match_odds_charts_market_label
  ON match_odds_charts (market, label);
CREATE INDEX IF NOT EXISTS idx_match_odds_charts_fetched_at
  ON match_odds_charts (fetched_at DESC);

GRANT SELECT, INSERT, UPDATE ON match_odds_charts TO anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE match_odds_charts_id_seq TO anon, authenticated;

ALTER TABLE match_odds_charts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_match_odds_charts" ON match_odds_charts;
CREATE POLICY "anon_select_match_odds_charts"
  ON match_odds_charts FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "anon_insert_match_odds_charts" ON match_odds_charts;
CREATE POLICY "anon_insert_match_odds_charts"
  ON match_odds_charts FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_match_odds_charts" ON match_odds_charts;
CREATE POLICY "anon_update_match_odds_charts"
  ON match_odds_charts FOR UPDATE TO anon USING (true) WITH CHECK (true);

COMMENT ON TABLE match_odds_charts IS
  'Saved userbet odds chart series (graph_lineups) keyed by our match_id + external fixture + book/market/label.';
COMMENT ON COLUMN match_odds_charts.match_id IS
  'Our matches.id / v_matches_full.match_id';
COMMENT ON COLUMN match_odds_charts.external_match_id IS
  'userbet ps_id / id_fixture';
COMMENT ON COLUMN match_odds_charts.points IS
  'JSON array of {kf, dt, oddtime, bet, ...} from SVG circle points';

-- Example upsert:
-- INSERT INTO match_odds_charts (
--   match_id, external_match_id, bookmaker, bookmaker_id, market, label, points, source_url
-- ) VALUES (
--   14180,
--   '1640270708',
--   'PS3838',
--   70,
--   '1X2',
--   '1',
--   '[
--      {"kf": 1.69, "dt": "17.11 12:41", "oddtime": 1794908499, "bet": 75},
--      {"kf": 2.08, "dt": "17.11 12:49", "oddtime": 1794908990, "bet": 75}
--    ]'::jsonb,
--   'https://userbet.info/user/graph_lineups?ps_id=1640270708&id_market=1&id_bookmaker=70&label=1'
-- )
-- ON CONFLICT (match_id, external_match_id, bookmaker, market, label)
-- DO UPDATE SET
--   points = EXCLUDED.points,
--   bookmaker_id = EXCLUDED.bookmaker_id,
--   source_url = EXCLUDED.source_url,
--   fetched_at = now(),
--   updated_at = now();

-- After create: Supabase → Project Settings → API → Reload schema (если PostgREST ещё отдаёт 404).
