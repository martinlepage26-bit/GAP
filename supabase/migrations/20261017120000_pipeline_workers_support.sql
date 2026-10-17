/*
# Pipeline workers support

1. review_extractions
   - One extraction job can yield several atomic items. Replace the
     one-row-per-job constraint with (job_id, item_index).
2. extraction_embeddings
   - Embeddings of each extracted complaint, used by the cluster worker.
3. gap_cluster_assessments
   - Optional owner judgement for the two components the corpus cannot
     measure (feasibility, defensibility) and for the incumbent-fix penalty.
     The score worker uses 0.5 / 0.5 / 0 when no row exists.
4. claim_extraction_jobs / claim_queue_jobs
   - Their RETURNS TABLE column "id" collided with the table column inside
     the UPDATE, so Postgres rejected every call ("column reference id is
     ambiguous") and no worker could claim a job. Recreated with
     #variable_conflict use_column; bodies otherwise unchanged. CREATE OR
     REPLACE keeps the F13/F14 revokes.
5. Security
   - Both new tables are worker-only: RLS on, no policies, and no grants
     to anon or authenticated. service_role bypasses RLS.
*/

ALTER TABLE review_extractions
  ADD COLUMN IF NOT EXISTS item_index int NOT NULL DEFAULT 0;

ALTER TABLE review_extractions
  DROP CONSTRAINT IF EXISTS review_extractions_job_id_key;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'review_extractions_job_item_key'
  ) THEN
    ALTER TABLE review_extractions
      ADD CONSTRAINT review_extractions_job_item_key UNIQUE (job_id, item_index);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS extraction_embeddings (
  extraction_id bigint PRIMARY KEY REFERENCES review_extractions(id) ON DELETE CASCADE,
  model_key text NOT NULL,
  embedding vector(384) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gap_cluster_assessments (
  gap_cluster_id bigint PRIMARY KEY REFERENCES gap_clusters(id) ON DELETE CASCADE,
  feasibility_score numeric(6,5) NOT NULL DEFAULT 0.5 CHECK (feasibility_score BETWEEN 0 AND 1),
  defensibility_score numeric(6,5) NOT NULL DEFAULT 0.5 CHECK (defensibility_score BETWEEN 0 AND 1),
  incumbent_fix_penalty numeric(6,5) NOT NULL DEFAULT 0 CHECK (incumbent_fix_penalty BETWEEN 0 AND 0.2),
  note text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_review_extractions_review ON review_extractions(review_id);
CREATE INDEX IF NOT EXISTS idx_queue_jobs_running ON queue_jobs(status, updated_at) WHERE status = 'running';

ALTER TABLE extraction_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE gap_cluster_assessments ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON extraction_embeddings FROM anon, authenticated;
REVOKE ALL ON gap_cluster_assessments FROM anon, authenticated;

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
#variable_conflict use_column
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
#variable_conflict use_column
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
