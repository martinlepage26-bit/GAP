/*
# F2 — Make normalized_reviews read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - Normalization is performed by the worker processes, which connect as
     `service_role` and keep full access.
*/

DROP POLICY IF EXISTS "anon_insert_normalized_reviews" ON normalized_reviews;
DROP POLICY IF EXISTS "anon_update_normalized_reviews" ON normalized_reviews;
DROP POLICY IF EXISTS "anon_delete_normalized_reviews" ON normalized_reviews;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON normalized_reviews FROM anon, authenticated;
