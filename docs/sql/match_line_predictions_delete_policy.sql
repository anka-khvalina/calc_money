-- Allow anon DELETE on match_line_predictions (needed for «Удалить» on Прогнозы tab).
-- Run in Supabase SQL Editor.
--
-- Without this policy PostgREST returns HTTP 200 and 0 rows — UI shows an error.

ALTER TABLE match_line_predictions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_delete_match_line_predictions" ON match_line_predictions;
CREATE POLICY "anon_delete_match_line_predictions"
  ON match_line_predictions
  FOR DELETE
  TO anon
  USING (true);

-- Optional: remove leftover probe rows from earlier tests
DELETE FROM match_line_predictions
WHERE season_label LIKE '__probe%';
