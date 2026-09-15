/*
# F14 — Remove public execute rights on the remaining pipeline routines

1. Security changes
   - Revoke EXECUTE from PUBLIC, `anon` and `authenticated` on
     `claim_queue_jobs`, `upsert_extraction_metrics`,
     `find_semantic_duplicates` and `compute_gap_score`.
2. Notes
   - `claim_queue_jobs` and `upsert_extraction_metrics` mutate pipeline state;
     the other two are read/compute helpers that the browser never calls.
   - The workers connect as `service_role` and keep execute rights.
*/

REVOKE ALL ON FUNCTION public.claim_queue_jobs(text, integer)
  FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.upsert_extraction_metrics(
  text, integer, integer, integer, bigint, bigint, numeric, integer, integer, integer)
  FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.find_semantic_duplicates(bigint, double precision, integer)
  FROM PUBLIC, anon, authenticated;

REVOKE ALL ON FUNCTION public.compute_gap_score(
  double precision, double precision, double precision, double precision,
  double precision, double precision, double precision, double precision, double precision)
  FROM PUBLIC, anon, authenticated;
