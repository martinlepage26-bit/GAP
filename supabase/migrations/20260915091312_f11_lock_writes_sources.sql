/*
# F11 — Make sources read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - `trust_weight` is scoring configuration; a caller able to edit it can
     re-rank every gap the engine produces.
*/

DROP POLICY IF EXISTS "anon_insert_sources" ON sources;
DROP POLICY IF EXISTS "anon_update_sources" ON sources;
DROP POLICY IF EXISTS "anon_delete_sources" ON sources;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON sources FROM anon, authenticated;
