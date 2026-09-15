/*
# F4 — Make the duplicate grouping tables read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies on `duplicate_groups` and
     `duplicate_group_members` for anon and authenticated.
   - Revoke write privileges on both tables from `anon` and `authenticated`.
2. Notes
   - SELECT stays in place: the dashboard reads both tables to show duplicate
     group counts.
*/

DROP POLICY IF EXISTS "anon_insert_duplicate_groups" ON duplicate_groups;
DROP POLICY IF EXISTS "anon_update_duplicate_groups" ON duplicate_groups;
DROP POLICY IF EXISTS "anon_delete_duplicate_groups" ON duplicate_groups;

DROP POLICY IF EXISTS "anon_insert_dgm" ON duplicate_group_members;
DROP POLICY IF EXISTS "anon_update_dgm" ON duplicate_group_members;
DROP POLICY IF EXISTS "anon_delete_dgm" ON duplicate_group_members;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON duplicate_groups FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON duplicate_group_members FROM anon, authenticated;
