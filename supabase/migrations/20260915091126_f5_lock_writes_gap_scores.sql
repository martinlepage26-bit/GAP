/*
# F5 — Make gap_scores read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - Scores are the product output and must only be produced by the scoring
     worker; SELECT is preserved because the dashboard displays them.
*/

DROP POLICY IF EXISTS "anon_insert_gap_scores" ON gap_scores;
DROP POLICY IF EXISTS "anon_update_gap_scores" ON gap_scores;
DROP POLICY IF EXISTS "anon_delete_gap_scores" ON gap_scores;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON gap_scores FROM anon, authenticated;
