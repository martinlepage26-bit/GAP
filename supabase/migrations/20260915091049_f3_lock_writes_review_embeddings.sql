/*
# F3 — Make review_embeddings read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies for anon and authenticated.
   - Revoke write privileges from `anon` and `authenticated`.
2. Notes
   - Embeddings drive semantic deduplication; allowing the browser role to
     overwrite them let anyone control which reviews look like duplicates.
*/

DROP POLICY IF EXISTS "anon_insert_review_embeddings" ON review_embeddings;
DROP POLICY IF EXISTS "anon_update_review_embeddings" ON review_embeddings;
DROP POLICY IF EXISTS "anon_delete_review_embeddings" ON review_embeddings;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON review_embeddings FROM anon, authenticated;
