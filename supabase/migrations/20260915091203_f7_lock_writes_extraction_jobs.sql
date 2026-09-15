/*
# F7 — Make extraction_jobs read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - Anyone could previously insert jobs, or reset finished jobs to "queued",
     and the extraction worker would spend money on a paid model for each one.
   - SELECT is preserved: the dashboard shows queue depth by job type.
*/

DROP POLICY IF EXISTS "anon_insert_extraction_jobs" ON extraction_jobs;
DROP POLICY IF EXISTS "anon_update_extraction_jobs" ON extraction_jobs;
DROP POLICY IF EXISTS "anon_delete_extraction_jobs" ON extraction_jobs;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON extraction_jobs FROM anon, authenticated;
