-- Fix RLS/grants for match_odds_charts (run if anon INSERT returns 401 / 42501).
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
