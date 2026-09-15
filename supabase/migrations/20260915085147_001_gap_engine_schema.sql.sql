/*
# Gap Discovery Engine - Core Schema

## Overview
Creates the full normalized review pipeline schema for a market gap analysis tool.
This schema supports: raw document ingestion, normalization, LLM-based extraction,
pgvector semantic deduplication, gap clustering, and transparent scoring.

## Tables Created
1. sources - Registry of data sources (reddit, g2, app_store, etc.)
2. ingestion_runs - Tracking for batch ingestion jobs
3. raw_documents - Immutable raw payloads from scrapers
4. normalized_reviews - Cleaned, deduplicated review records
5. review_embeddings - pgvector embeddings for semantic dedup
6. duplicate_groups - Groups of semantically similar reviews
7. duplicate_group_members - Membership in duplicate groups
8. extraction_jobs - Queue table for LLM extraction (SKIP LOCKED pattern)
9. review_extractions - Structured LLM extraction results with token/cost tracking
10. gap_clusters - Clusters of similar extracted complaints
11. gap_cluster_members - Membership linking extractions to clusters
12. gap_scores - Transparent weighted scoring per cluster
13. extraction_metrics - Per-run cost/token/rate-limit monitoring
14. queue_jobs - Generic background batch queue for workers

## Functions
- claim_extraction_jobs() - Atomic SKIP LOCKED job claiming
- find_semantic_duplicates() - pgvector nearest-neighbor dedup query
- compute_gap_score() - Transparent weighted scoring formula

## Security
- RLS enabled on all tables
- TO anon, authenticated (single-tenant, no auth screen)
- All CRUD allowed for pipeline operations
*/

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. sources
-- ============================================================
CREATE TABLE IF NOT EXISTS sources (
  id bigserial PRIMARY KEY,
  source_key text UNIQUE NOT NULL,
  display_name text NOT NULL,
  trust_weight numeric(4,3) NOT NULL DEFAULT 1.000,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_sources" ON sources;
CREATE POLICY "anon_select_sources" ON sources FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_sources" ON sources;
CREATE POLICY "anon_insert_sources" ON sources FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_sources" ON sources;
CREATE POLICY "anon_update_sources" ON sources FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_sources" ON sources;
CREATE POLICY "anon_delete_sources" ON sources FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 2. ingestion_runs
-- ============================================================
CREATE TABLE IF NOT EXISTS ingestion_runs (
  id bigserial PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES sources(id),
  scope_key text NOT NULL,
  run_type text NOT NULL CHECK (run_type IN ('recent','backfill','reparse')),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed')),
  cursor_json jsonb,
  stats_json jsonb,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE ingestion_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_ingestion_runs" ON ingestion_runs;
CREATE POLICY "anon_select_ingestion_runs" ON ingestion_runs FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_ingestion_runs" ON ingestion_runs;
CREATE POLICY "anon_insert_ingestion_runs" ON ingestion_runs FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_ingestion_runs" ON ingestion_runs;
CREATE POLICY "anon_update_ingestion_runs" ON ingestion_runs FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_ingestion_runs" ON ingestion_runs;
CREATE POLICY "anon_delete_ingestion_runs" ON ingestion_runs FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 3. raw_documents - immutable raw payloads
-- ============================================================
CREATE TABLE IF NOT EXISTS raw_documents (
  id bigserial PRIMARY KEY,
  source_id bigint NOT NULL REFERENCES sources(id),
  external_id text NOT NULL,
  parent_external_id text,
  canonical_url text,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  authored_at timestamptz,
  item_type text NOT NULL CHECK (item_type IN ('review','post','comment','pricing_page','forum_thread')),
  title text,
  raw_text text,
  raw_payload jsonb NOT NULL,
  parser_version text NOT NULL,
  content_hash bytea NOT NULL,
  unique (source_id, external_id)
);

ALTER TABLE raw_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_raw_documents" ON raw_documents;
CREATE POLICY "anon_select_raw_documents" ON raw_documents FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_raw_documents" ON raw_documents;
CREATE POLICY "anon_insert_raw_documents" ON raw_documents FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_raw_documents" ON raw_documents;
CREATE POLICY "anon_update_raw_documents" ON raw_documents FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_raw_documents" ON raw_documents;
CREATE POLICY "anon_delete_raw_documents" ON raw_documents FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 4. normalized_reviews - cleaned, versioned records
-- ============================================================
CREATE TABLE IF NOT EXISTS normalized_reviews (
  id bigserial PRIMARY KEY,
  raw_document_id bigint NOT NULL UNIQUE REFERENCES raw_documents(id) ON DELETE CASCADE,
  source_id bigint NOT NULL REFERENCES sources(id),
  source_record_version int NOT NULL DEFAULT 1,
  product_name text,
  competitor_name text,
  rating_5 numeric(3,2),
  language text,
  country text,
  author_hash text,
  title text,
  body_text text NOT NULL,
  combined_text text GENERATED ALWAYS AS (
    trim(coalesce(title,'') || E'\n' || coalesce(body_text,''))
  ) STORED,
  metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  exact_dedupe_hash bytea NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE normalized_reviews ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_normalized_reviews" ON normalized_reviews;
CREATE POLICY "anon_select_normalized_reviews" ON normalized_reviews FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_normalized_reviews" ON normalized_reviews;
CREATE POLICY "anon_insert_normalized_reviews" ON normalized_reviews FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_normalized_reviews" ON normalized_reviews;
CREATE POLICY "anon_update_normalized_reviews" ON normalized_reviews FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_normalized_reviews" ON normalized_reviews;
CREATE POLICY "anon_delete_normalized_reviews" ON normalized_reviews FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 5. review_embeddings - pgvector for semantic dedup
-- ============================================================
CREATE TABLE IF NOT EXISTS review_embeddings (
  review_id bigint PRIMARY KEY REFERENCES normalized_reviews(id) ON DELETE CASCADE,
  model_key text NOT NULL,
  embedding vector(384) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE review_embeddings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_review_embeddings" ON review_embeddings;
CREATE POLICY "anon_select_review_embeddings" ON review_embeddings FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_review_embeddings" ON review_embeddings;
CREATE POLICY "anon_insert_review_embeddings" ON review_embeddings FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_review_embeddings" ON review_embeddings;
CREATE POLICY "anon_update_review_embeddings" ON review_embeddings FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_review_embeddings" ON review_embeddings;
CREATE POLICY "anon_delete_review_embeddings" ON review_embeddings FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 6. duplicate_groups
-- ============================================================
CREATE TABLE IF NOT EXISTS duplicate_groups (
  id bigserial PRIMARY KEY,
  group_type text NOT NULL CHECK (group_type IN ('exact','semantic','boilerplate')),
  canonical_review_id bigint REFERENCES normalized_reviews(id) ON DELETE SET NULL,
  confidence numeric(4,3) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE duplicate_groups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_duplicate_groups" ON duplicate_groups;
CREATE POLICY "anon_select_duplicate_groups" ON duplicate_groups FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_duplicate_groups" ON duplicate_groups;
CREATE POLICY "anon_insert_duplicate_groups" ON duplicate_groups FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_duplicate_groups" ON duplicate_groups;
CREATE POLICY "anon_update_duplicate_groups" ON duplicate_groups FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_duplicate_groups" ON duplicate_groups;
CREATE POLICY "anon_delete_duplicate_groups" ON duplicate_groups FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 7. duplicate_group_members
-- ============================================================
CREATE TABLE IF NOT EXISTS duplicate_group_members (
  duplicate_group_id bigint NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
  review_id bigint NOT NULL REFERENCES normalized_reviews(id) ON DELETE CASCADE,
  match_score numeric(6,5),
  reason_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (duplicate_group_id, review_id)
);

ALTER TABLE duplicate_group_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_dgm" ON duplicate_group_members;
CREATE POLICY "anon_select_dgm" ON duplicate_group_members FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_dgm" ON duplicate_group_members;
CREATE POLICY "anon_insert_dgm" ON duplicate_group_members FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_dgm" ON duplicate_group_members;
CREATE POLICY "anon_update_dgm" ON duplicate_group_members FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_dgm" ON duplicate_group_members;
CREATE POLICY "anon_delete_dgm" ON duplicate_group_members FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 8. extraction_jobs - LLM extraction queue (SKIP LOCKED)
-- ============================================================
CREATE TABLE IF NOT EXISTS extraction_jobs (
  id bigserial PRIMARY KEY,
  review_id bigint NOT NULL REFERENCES normalized_reviews(id) ON DELETE CASCADE,
  job_type text NOT NULL CHECK (job_type IN ('complaint_extract','segment_tag','score_signals')),
  model_key text NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','retry','failed','done')),
  priority int NOT NULL DEFAULT 100,
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 3,
  available_at timestamptz NOT NULL DEFAULT now(),
  idempotency_key text UNIQUE NOT NULL,
  prompt_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE extraction_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_extraction_jobs" ON extraction_jobs;
CREATE POLICY "anon_select_extraction_jobs" ON extraction_jobs FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_extraction_jobs" ON extraction_jobs;
CREATE POLICY "anon_insert_extraction_jobs" ON extraction_jobs FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_extraction_jobs" ON extraction_jobs;
CREATE POLICY "anon_update_extraction_jobs" ON extraction_jobs FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_extraction_jobs" ON extraction_jobs;
CREATE POLICY "anon_delete_extraction_jobs" ON extraction_jobs FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 9. review_extractions - structured LLM output + cost tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS review_extractions (
  id bigserial PRIMARY KEY,
  review_id bigint NOT NULL REFERENCES normalized_reviews(id) ON DELETE CASCADE,
  job_id bigint NOT NULL UNIQUE REFERENCES extraction_jobs(id) ON DELETE CASCADE,
  schema_version text NOT NULL,
  model_key text NOT NULL,
  extraction_json jsonb NOT NULL,
  validation_errors jsonb,
  token_input int,
  token_output int,
  cost_usd numeric(10,6),
  latency_ms int,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE review_extractions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_review_extractions" ON review_extractions;
CREATE POLICY "anon_select_review_extractions" ON review_extractions FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_review_extractions" ON review_extractions;
CREATE POLICY "anon_insert_review_extractions" ON review_extractions FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_review_extractions" ON review_extractions;
CREATE POLICY "anon_update_review_extractions" ON review_extractions FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_review_extractions" ON review_extractions;
CREATE POLICY "anon_delete_review_extractions" ON review_extractions FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 10. gap_clusters
-- ============================================================
CREATE TABLE IF NOT EXISTS gap_clusters (
  id bigserial PRIMARY KEY,
  market_key text NOT NULL,
  category text NOT NULL CHECK (category IN ('feature_gap','price_gap','segment_gap','geography_language_gap','quality_gap','other')),
  cluster_label text,
  summary text,
  status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','merged','retired')),
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE gap_clusters ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_gap_clusters" ON gap_clusters;
CREATE POLICY "anon_select_gap_clusters" ON gap_clusters FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_gap_clusters" ON gap_clusters;
CREATE POLICY "anon_insert_gap_clusters" ON gap_clusters FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_gap_clusters" ON gap_clusters;
CREATE POLICY "anon_update_gap_clusters" ON gap_clusters FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_gap_clusters" ON gap_clusters;
CREATE POLICY "anon_delete_gap_clusters" ON gap_clusters FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 11. gap_cluster_members
-- ============================================================
CREATE TABLE IF NOT EXISTS gap_cluster_members (
  gap_cluster_id bigint NOT NULL REFERENCES gap_clusters(id) ON DELETE CASCADE,
  extraction_id bigint NOT NULL REFERENCES review_extractions(id) ON DELETE CASCADE,
  similarity_score numeric(6,5),
  PRIMARY KEY (gap_cluster_id, extraction_id)
);

ALTER TABLE gap_cluster_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_gcm" ON gap_cluster_members;
CREATE POLICY "anon_select_gcm" ON gap_cluster_members FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_gcm" ON gap_cluster_members;
CREATE POLICY "anon_insert_gcm" ON gap_cluster_members FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_gcm" ON gap_cluster_members;
CREATE POLICY "anon_update_gcm" ON gap_cluster_members FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_gcm" ON gap_cluster_members;
CREATE POLICY "anon_delete_gcm" ON gap_cluster_members FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 12. gap_scores - transparent weighted scoring
-- ============================================================
CREATE TABLE IF NOT EXISTS gap_scores (
  gap_cluster_id bigint PRIMARY KEY REFERENCES gap_clusters(id) ON DELETE CASCADE,
  algorithm_version text NOT NULL,
  frequency_score numeric(6,5) NOT NULL,
  intensity_score numeric(6,5) NOT NULL,
  breadth_score numeric(6,5) NOT NULL,
  wtp_score numeric(6,5) NOT NULL,
  workaround_score numeric(6,5) NOT NULL,
  segment_score numeric(6,5) NOT NULL,
  feasibility_score numeric(6,5) NOT NULL,
  defensibility_score numeric(6,5) NOT NULL,
  penalty_score numeric(6,5) NOT NULL DEFAULT 0,
  total_score numeric(7,5) NOT NULL,
  computed_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE gap_scores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_gap_scores" ON gap_scores;
CREATE POLICY "anon_select_gap_scores" ON gap_scores FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_gap_scores" ON gap_scores;
CREATE POLICY "anon_insert_gap_scores" ON gap_scores FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_gap_scores" ON gap_scores;
CREATE POLICY "anon_update_gap_scores" ON gap_scores FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_gap_scores" ON gap_scores;
CREATE POLICY "anon_delete_gap_scores" ON gap_scores FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 13. extraction_metrics - per-run cost/token/rate-limit monitoring
-- ============================================================
CREATE TABLE IF NOT EXISTS extraction_metrics (
  id bigserial PRIMARY KEY,
  run_date date NOT NULL,
  model_key text NOT NULL,
  total_requests int NOT NULL DEFAULT 0,
  successful_extractions int NOT NULL DEFAULT 0,
  failed_extractions int NOT NULL DEFAULT 0,
  validation_failures int NOT NULL DEFAULT 0,
  total_tokens_input bigint NOT NULL DEFAULT 0,
  total_tokens_output bigint NOT NULL DEFAULT 0,
  total_cost_usd numeric(12,6) NOT NULL DEFAULT 0,
  rate_limit_hits int NOT NULL DEFAULT 0,
  avg_latency_ms int,
  retry_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_date, model_key)
);

ALTER TABLE extraction_metrics ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_extraction_metrics" ON extraction_metrics;
CREATE POLICY "anon_select_extraction_metrics" ON extraction_metrics FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_extraction_metrics" ON extraction_metrics;
CREATE POLICY "anon_insert_extraction_metrics" ON extraction_metrics FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_extraction_metrics" ON extraction_metrics;
CREATE POLICY "anon_update_extraction_metrics" ON extraction_metrics FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_extraction_metrics" ON extraction_metrics;
CREATE POLICY "anon_delete_extraction_metrics" ON extraction_metrics FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- 14. queue_jobs - generic background batch queue
-- ============================================================
CREATE TABLE IF NOT EXISTS queue_jobs (
  id bigserial PRIMARY KEY,
  queue_name text NOT NULL,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','retry','failed','done')),
  priority int NOT NULL DEFAULT 100,
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 3,
  available_at timestamptz NOT NULL DEFAULT now(),
  idempotency_key text UNIQUE,
  result_json jsonb,
  error_text text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE queue_jobs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon_select_queue_jobs" ON queue_jobs;
CREATE POLICY "anon_select_queue_jobs" ON queue_jobs FOR SELECT TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_queue_jobs" ON queue_jobs;
CREATE POLICY "anon_insert_queue_jobs" ON queue_jobs FOR INSERT TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_queue_jobs" ON queue_jobs;
CREATE POLICY "anon_update_queue_jobs" ON queue_jobs FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_queue_jobs" ON queue_jobs;
CREATE POLICY "anon_delete_queue_jobs" ON queue_jobs FOR DELETE TO anon, authenticated USING (true);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_raw_documents_source_authored ON raw_documents(source_id, authored_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_documents_content_hash ON raw_documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_normalized_reviews_source_product ON normalized_reviews(source_id, competitor_name, product_name);
CREATE INDEX IF NOT EXISTS idx_normalized_reviews_hash ON normalized_reviews(exact_dedupe_hash);
CREATE INDEX IF NOT EXISTS idx_normalized_reviews_combined_text_trgm ON normalized_reviews USING gin (combined_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_extraction_jobs_queue ON extraction_jobs(status, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_review_embeddings_hnsw ON review_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_gap_cluster_members_extraction ON gap_cluster_members(extraction_id);
CREATE INDEX IF NOT EXISTS idx_queue_jobs_status ON queue_jobs(queue_name, status, available_at, priority);
CREATE INDEX IF NOT EXISTS idx_extraction_metrics_date ON extraction_metrics(run_date DESC, model_key);

-- ============================================================
-- Function: claim_extraction_jobs
-- Atomic SKIP LOCKED job claiming for extraction workers
-- ============================================================
CREATE OR REPLACE FUNCTION claim_extraction_jobs(
  p_worker_id text,
  p_limit int DEFAULT 50,
  p_job_type text DEFAULT NULL
)
RETURNS TABLE (
  id bigint,
  review_id bigint,
  job_type text,
  model_key text,
  prompt_version text,
  attempts int
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  UPDATE extraction_jobs
  SET status = 'running',
      attempts = attempts + 1,
      updated_at = now()
  WHERE id IN (
    SELECT ej.id
    FROM extraction_jobs ej
    WHERE ej.status IN ('queued','retry')
      AND ej.available_at <= now()
      AND (p_job_type IS NULL OR ej.job_type = p_job_type)
    ORDER BY ej.priority ASC, ej.created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT LEAST(p_limit, 200)
  )
  RETURNING
    extraction_jobs.id,
    extraction_jobs.review_id,
    extraction_jobs.job_type,
    extraction_jobs.model_key,
    extraction_jobs.prompt_version,
    extraction_jobs.attempts;
END;
$$;

-- ============================================================
-- Function: claim_queue_jobs
-- Atomic SKIP LOCKED job claiming for generic queue
-- ============================================================
CREATE OR REPLACE FUNCTION claim_queue_jobs(
  p_queue_name text,
  p_limit int DEFAULT 50
)
RETURNS TABLE (
  id bigint,
  payload jsonb,
  attempts int
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  UPDATE queue_jobs
  SET status = 'running',
      attempts = attempts + 1,
      updated_at = now()
  WHERE id IN (
    SELECT qj.id
    FROM queue_jobs qj
    WHERE qj.queue_name = p_queue_name
      AND qj.status IN ('queued','retry')
      AND qj.available_at <= now()
    ORDER BY qj.priority ASC, qj.created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT LEAST(p_limit, 200)
  )
  RETURNING
    queue_jobs.id,
    queue_jobs.payload,
    queue_jobs.attempts;
END;
$$;

-- ============================================================
-- Function: find_semantic_duplicates
-- pgvector nearest-neighbor dedup with blocking filters
-- Returns candidate duplicates for a given review within
-- same language, same competitor, and same 30-day window
-- ============================================================
CREATE OR REPLACE FUNCTION find_semantic_duplicates(
  p_review_id bigint,
  p_similarity_threshold float DEFAULT 0.88,
  p_limit int DEFAULT 20
)
RETURNS TABLE (
  candidate_review_id bigint,
  cosine_similarity float,
  combined_text text,
  competitor_name text,
  product_name text
)
LANGUAGE sql
AS $$
  SELECT
    r2.review_id AS candidate_review_id,
    1 - (r1.embedding <=> r2.embedding) AS cosine_similarity,
    n2.combined_text,
    n2.competitor_name,
    n2.product_name
  FROM review_embeddings r1
  JOIN review_embeddings r2
    ON r1.review_id <> r2.review_id
    AND 1 - (r1.embedding <=> r2.embedding) >= p_similarity_threshold
  JOIN normalized_reviews n1
    ON n1.id = r1.review_id
  JOIN normalized_reviews n2
    ON n2.id = r2.review_id
  WHERE r1.review_id = p_review_id
    AND (n2.language IS NULL OR n2.language = n1.language)
    AND (n2.competitor_name IS NULL OR n2.competitor_name = n1.competitor_name)
    AND (
      n2.created_at IS NULL OR n1.created_at IS NULL OR
      ABS(EXTRACT(EPOCH FROM (n2.created_at - n1.created_at))) < 2592000
    )
  ORDER BY r1.embedding <=> r2.embedding
  LIMIT LEAST(p_limit, 100);
$$;

-- ============================================================
-- Function: compute_gap_score
-- Transparent weighted scoring formula for gap clusters
-- Weights: F=0.20, I=0.20, B=0.15, W=0.15, P=0.10, S=0.05, Fe=0.10, D=0.05
-- Then subtract penalty_score, clamp to [0, 1]
-- ============================================================
CREATE OR REPLACE FUNCTION compute_gap_score(
  p_frequency float,
  p_intensity float,
  p_breadth float,
  p_wtp float,
  p_workaround float,
  p_segment float,
  p_feasibility float,
  p_defensibility float,
  p_penalty float DEFAULT 0
)
RETURNS float
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT
    GREATEST(0.0, LEAST(1.0,
      0.20 * p_frequency +
      0.20 * p_intensity +
      0.15 * p_breadth +
      0.15 * p_wtp +
      0.10 * p_workaround +
      0.05 * p_segment +
      0.10 * p_feasibility +
      0.05 * p_defensibility -
      p_penalty
    ));
$$;

-- ============================================================
-- Function: upsert_extraction_metrics
-- Incremental metric tracking per day + model
-- ============================================================
CREATE OR REPLACE FUNCTION upsert_extraction_metrics(
  p_model_key text,
  p_success_count int DEFAULT 0,
  p_fail_count int DEFAULT 0,
  p_validation_fails int DEFAULT 0,
  p_tokens_in bigint DEFAULT 0,
  p_tokens_out bigint DEFAULT 0,
  p_cost numeric DEFAULT 0,
  p_rate_limit_hits int DEFAULT 0,
  p_latency_ms int DEFAULT 0,
  p_retries int DEFAULT 0
)
RETURNS void
LANGUAGE sql
AS $$
  INSERT INTO extraction_metrics (
    run_date, model_key, total_requests,
    successful_extractions, failed_extractions, validation_failures,
    total_tokens_input, total_tokens_output, total_cost_usd,
    rate_limit_hits, avg_latency_ms, retry_count
  )
  VALUES (
    CURRENT_DATE, p_model_key, p_success_count + p_fail_count,
    p_success_count, p_fail_count, p_validation_fails,
    p_tokens_in, p_tokens_out, p_cost,
    p_rate_limit_hits, p_latency_ms, p_retries
  )
  ON CONFLICT (run_date, model_key)
  DO UPDATE SET
    total_requests = extraction_metrics.total_requests + (p_success_count + p_fail_count),
    successful_extractions = extraction_metrics.successful_extractions + p_success_count,
    failed_extractions = extraction_metrics.failed_extractions + p_fail_count,
    validation_failures = extraction_metrics.validation_failures + p_validation_fails,
    total_tokens_input = extraction_metrics.total_tokens_input + p_tokens_in,
    total_tokens_output = extraction_metrics.total_tokens_output + p_tokens_out,
    total_cost_usd = extraction_metrics.total_cost_usd + p_cost,
    rate_limit_hits = extraction_metrics.rate_limit_hits + p_rate_limit_hits,
    retry_count = extraction_metrics.retry_count + p_retries,
    avg_latency_ms = CASE
      WHEN extraction_metrics.total_requests + (p_success_count + p_fail_count) = 0 THEN p_latency_ms
      ELSE (extraction_metrics.avg_latency_ms * extraction_metrics.total_requests + p_latency_ms * (p_success_count + p_fail_count))
           / NULLIF(extraction_metrics.total_requests + (p_success_count + p_fail_count), 0)
    END;
$$;

-- ============================================================
-- Seed default sources
-- ============================================================
INSERT INTO sources (source_key, display_name, trust_weight) VALUES
  ('reddit', 'Reddit', 0.800),
  ('g2', 'G2 Crowd', 0.950),
  ('app_store', 'Apple App Store', 0.900),
  ('play_store', 'Google Play Store', 0.900),
  ('quora', 'Quora', 0.700),
  ('hackernews', 'Hacker News', 0.750),
  ('capterra', 'Capterra', 0.900),
  ('pricing_page', 'Competitor Pricing Pages', 0.850)
ON CONFLICT (source_key) DO NOTHING;
