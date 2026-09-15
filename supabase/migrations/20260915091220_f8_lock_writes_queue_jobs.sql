/*
# F8 — Make queue_jobs read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - Job payloads are handed straight to the worker handlers, so the public
     role must not be able to create or edit them.
*/

DROP POLICY IF EXISTS "anon_insert_queue_jobs" ON queue_jobs;
DROP POLICY IF EXISTS "anon_update_queue_jobs" ON queue_jobs;
DROP POLICY IF EXISTS "anon_delete_queue_jobs" ON queue_jobs;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON queue_jobs FROM anon, authenticated;
