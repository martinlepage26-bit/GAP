/*
# F13 — Remove public execute rights on claim_extraction_jobs

1. Security changes
   - Revoke EXECUTE on `claim_extraction_jobs(text, integer, text)` from
     PUBLIC, `anon` and `authenticated`.
2. Notes
   - The routine mutates job rows (status, attempts) and was callable by any
     visitor through the data API, which allowed job theft and starvation.
   - The browser application calls no database routines; the workers connect
     as `service_role` and are unaffected.
*/

REVOKE ALL ON FUNCTION public.claim_extraction_jobs(text, integer, text)
  FROM PUBLIC, anon, authenticated;
