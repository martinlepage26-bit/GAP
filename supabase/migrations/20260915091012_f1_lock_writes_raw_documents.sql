/*
# F1 — Make raw_documents read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies that allowed the anon and
     authenticated roles to write any row of `raw_documents`.
   - Revoke INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES and TRIGGER from
     `anon` and `authenticated`.
2. Notes
   - `raw_documents` is the immutable source-snapshot ledger; nothing in the
     browser application writes it.
   - `service_role` (used by the Python workers via the database URL) keeps all
     privileges, so ingestion is unaffected.
*/

DROP POLICY IF EXISTS "anon_insert_raw_documents" ON raw_documents;
DROP POLICY IF EXISTS "anon_update_raw_documents" ON raw_documents;
DROP POLICY IF EXISTS "anon_delete_raw_documents" ON raw_documents;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON raw_documents FROM anon, authenticated;
