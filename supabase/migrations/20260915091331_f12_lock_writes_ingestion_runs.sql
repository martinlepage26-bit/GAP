/*
# F12 — Make ingestion_runs read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - `cursor_json` controls where incremental collection resumes; editing it
     from outside could make the collectors skip ranges of history.
*/

DROP POLICY IF EXISTS "anon_insert_ingestion_runs" ON ingestion_runs;
DROP POLICY IF EXISTS "anon_update_ingestion_runs" ON ingestion_runs;
DROP POLICY IF EXISTS "anon_delete_ingestion_runs" ON ingestion_runs;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON ingestion_runs FROM anon, authenticated;
