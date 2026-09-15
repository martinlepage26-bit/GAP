/*
# F9 — Make review_extractions read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - These rows link a source snippet to a scored gap and carry per-call cost
     figures; only the extraction worker may create them.
*/

DROP POLICY IF EXISTS "anon_insert_review_extractions" ON review_extractions;
DROP POLICY IF EXISTS "anon_update_review_extractions" ON review_extractions;
DROP POLICY IF EXISTS "anon_delete_review_extractions" ON review_extractions;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON review_extractions FROM anon, authenticated;
