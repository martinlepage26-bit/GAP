/*
# F10 — Make extraction_metrics read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - These rows are the only input to the cost, latency, rate-limit and drift
     alerts, so a public write path could silence every alert.
   - SELECT is preserved: the dashboard charts the last 30 days.
*/

DROP POLICY IF EXISTS "anon_insert_extraction_metrics" ON extraction_metrics;
DROP POLICY IF EXISTS "anon_update_extraction_metrics" ON extraction_metrics;
DROP POLICY IF EXISTS "anon_delete_extraction_metrics" ON extraction_metrics;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON extraction_metrics FROM anon, authenticated;
