/*
# F6 — Make the gap cluster tables read-only to the public API

1. Security changes
   - Drop the INSERT/UPDATE/DELETE policies on `gap_clusters` and
     `gap_cluster_members` for anon and authenticated.
   - Revoke write privileges on both tables from `anon` and `authenticated`.
2. Notes
   - `gap_clusters` keeps its SELECT policy because the dashboard lists active
     clusters; `gap_cluster_members` read access is handled separately.
*/

DROP POLICY IF EXISTS "anon_insert_gap_clusters" ON gap_clusters;
DROP POLICY IF EXISTS "anon_update_gap_clusters" ON gap_clusters;
DROP POLICY IF EXISTS "anon_delete_gap_clusters" ON gap_clusters;

DROP POLICY IF EXISTS "anon_insert_gcm" ON gap_cluster_members;
DROP POLICY IF EXISTS "anon_update_gcm" ON gap_cluster_members;
DROP POLICY IF EXISTS "anon_delete_gcm" ON gap_cluster_members;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON gap_clusters FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON gap_cluster_members FROM anon, authenticated;
