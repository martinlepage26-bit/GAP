export interface Source {
  id: number;
  source_key: string;
  display_name: string;
  trust_weight: number;
  created_at: string;
}

export interface ExtractionMetric {
  id: number;
  run_date: string;
  model_key: string;
  total_requests: number;
  successful_extractions: number;
  failed_extractions: number;
  validation_failures: number;
  total_tokens_input: number;
  total_tokens_output: number;
  total_cost_usd: number;
  rate_limit_hits: number;
  avg_latency_ms: number | null;
  retry_count: number;
}

export interface QueueStatus {
  job_type: string;
  status: string;
  count: number;
}

export interface GapCluster {
  id: number;
  market_key: string;
  category: string;
  cluster_label: string | null;
  summary: string | null;
  status: string;
  created_at: string;
  gap_scores?: GapScore | null;
}

export interface GapScore {
  gap_cluster_id: number;
  algorithm_version: string;
  frequency_score: number;
  intensity_score: number;
  breadth_score: number;
  wtp_score: number;
  workaround_score: number;
  segment_score: number;
  feasibility_score: number;
  defensibility_score: number;
  penalty_score: number;
  total_score: number;
  computed_at: string;
}

export interface DuplicateGroup {
  id: number;
  group_type: string;
  canonical_review_id: number | null;
  confidence: number;
  created_at: string;
  member_count?: number;
}

export interface ExtractionJob {
  id: number;
  review_id: number;
  job_type: string;
  model_key: string;
  status: string;
  priority: number;
  attempts: number;
  created_at: string;
  updated_at: string;
}

export interface ReviewExtraction {
  id: number;
  review_id: number;
  schema_version: string;
  model_key: string;
  extraction_json: Record<string, unknown>;
  token_input: number | null;
  token_output: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface IngestionRun {
  id: number;
  source_id: number;
  scope_key: string;
  run_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}
