-- Fix: Table Editor shows rows, but app/anon API returns [].
-- Cause: RLS without usable SELECT policy for anon.
-- Run in Supabase SQL Editor, then reopen the chart modal.

GRANT SELECT, INSERT, UPDATE ON TABLE public.match_odds_charts TO anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE public.match_odds_charts_id_seq TO anon, authenticated;

ALTER TABLE public.match_odds_charts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_match_odds_charts" ON public.match_odds_charts;
DROP POLICY IF EXISTS "anon_insert_match_odds_charts" ON public.match_odds_charts;
DROP POLICY IF EXISTS "anon_update_match_odds_charts" ON public.match_odds_charts;
DROP POLICY IF EXISTS "authenticated_select_match_odds_charts" ON public.match_odds_charts;
DROP POLICY IF EXISTS "authenticated_insert_match_odds_charts" ON public.match_odds_charts;
DROP POLICY IF EXISTS "authenticated_update_match_odds_charts" ON public.match_odds_charts;

CREATE POLICY "anon_select_match_odds_charts"
  ON public.match_odds_charts FOR SELECT TO anon USING (true);
CREATE POLICY "anon_insert_match_odds_charts"
  ON public.match_odds_charts FOR INSERT TO anon WITH CHECK (true);
CREATE POLICY "anon_update_match_odds_charts"
  ON public.match_odds_charts FOR UPDATE TO anon USING (true) WITH CHECK (true);

CREATE POLICY "authenticated_select_match_odds_charts"
  ON public.match_odds_charts FOR SELECT TO authenticated USING (true);
CREATE POLICY "authenticated_insert_match_odds_charts"
  ON public.match_odds_charts FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "authenticated_update_match_odds_charts"
  ON public.match_odds_charts FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- Sanity check (should return 7 for match 14066 after this):
-- select count(*) from public.match_odds_charts where match_id = 14066;
