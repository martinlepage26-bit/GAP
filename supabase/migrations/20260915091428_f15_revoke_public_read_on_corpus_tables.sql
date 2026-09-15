/*
# F15 — Make the collected corpus private

1. Security changes
   - Drop the SELECT policies and revoke SELECT from `anon` and
     `authenticated` on the tables the dashboard never queries:
     `raw_documents`, `normalized_reviews`, `review_embeddings`,
     `review_extractions`, `ingestion_runs`, `gap_cluster_members`, `sources`.
2. Notes
   - The dashboard reads only `extraction_metrics`, `extraction_jobs`,
     `gap_clusters`, `gap_scores`, `duplicate_groups` and
     `duplicate_group_members`; those keep their public read access so the
     interface continues to work unchanged.
   - `service_role` retains full read access for the worker processes.
*/

DROP POLICY IF EXISTS "anon_select_raw_documents" ON raw_documents;
DROP POLICY IF EXISTS "anon_select_normalized_reviews" ON normalized_reviews;
DROP POLICY IF EXISTS "anon_select_review_embeddings" ON review_embeddings;
DROP POLICY IF EXISTS "anon_select_review_extractions" ON review_extractions;
DROP POLICY IF EXISTS "anon_select_ingestion_runs" ON ingestion_runs;
DROP POLICY IF EXISTS "anon_select_gcm" ON gap_cluster_members;
DROP POLICY IF EXISTS "anon_select_sources" ON sources;

REVOKE SELECT ON raw_documents FROM anon, authenticated;
REVOKE SELECT ON normalized_reviews FROM anon, authenticated;
REVOKE SELECT ON review_embeddings FROM anon, authenticated;
REVOKE SELECT ON review_extractions FROM anon, authenticated;
REVOKE SELECT ON ingestion_runs FROM anon, authenticated;
REVOKE SELECT ON gap_cluster_members FROM anon, authenticated;
REVOKE SELECT ON sources FROM anon, authenticated;
