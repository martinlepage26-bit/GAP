"""Background batch queue workers using Postgres SKIP LOCKED.

Pipeline (each stage queues the next):

    ingest ─► normalize ─► embed ─► extract ─► cluster ─► score
    (queue_jobs)                    (extraction_jobs)

    ingest     public sources -> raw_documents                       (ingest.py)
    normalize  clean text, language, exact dedupe -> normalized_reviews (normalize.py)
    embed      384-d embeddings + semantic dedupe -> extraction job  (embed.py)
    extract    LLM atomic complaints -> review_extractions           (extract.py)
    cluster    complaints -> gap_clusters per market                 (cluster.py)
    score      weighted GapScore -> gap_scores                       (score.py)

It supports two queue patterns:

1. **Postgres-native SKIP LOCKED queue** (default, no external deps)
   - Uses claim_extraction_jobs() / claim_queue_jobs() functions
   - Claims are committed immediately; jobs left 'running' by a crashed
     worker return to 'retry' after --stale-minutes
   - Idempotency keys prevent duplicate work on retries

2. **Celery integration** (optional, for Python teams needing distributed workers)
   - Shows how to wrap the same logic in Celery tasks
   - Uses Redis as broker, Postgres as result backend

3. **BullMQ integration** (optional, for JS/TS teams)
   - Shows the equivalent queue setup in TypeScript for comparison

Usage:
    python -m gap_engine.worker --queue extract --model gpt-4.1-mini
    python -m gap_engine.worker --queue ingest
    python -m gap_engine.worker --queue normalize
    python -m gap_engine.worker --queue score
    python -m gap_engine.worker --queue all --drain     # every stage, exit when idle
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Callable, Optional

from psycopg2.extras import RealDictCursor

from .db import enqueue_if_idle, get_db_connection, requeue_stale
from .extract import (
    ExtractionMonitor,
    TokenBucket,
    extract_feedback,
    flush_monitor_to_db,
    make_idempotency_key,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)

logger = logging.getLogger(__name__)

__all__ = ["get_db_connection", "enqueue_extraction_job", "extraction_worker", "generic_queue_worker"]

STAGES = ["ingest", "normalize", "embed", "extract", "cluster", "score"]


# ─── Queue Helpers ─────────────────────────────────────────────────────────


def complete_job(conn, job_id: int, result_json: Optional[str] = None) -> None:
    """Mark an extraction job as done."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE extraction_jobs SET status='done', updated_at=now() WHERE id=%s",
            (job_id,),
        )
        conn.commit()


def fail_job(conn, job_id: int, error: str, max_attempts: int = 3) -> None:
    """Mark a job as failed or schedule retry with exponential backoff."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT attempts, max_attempts FROM extraction_jobs WHERE id=%s",
            (job_id,),
        )
        row = cur.fetchone()
        if row and row[0] >= row[1]:
            cur.execute(
                "UPDATE extraction_jobs SET status='failed', updated_at=now() WHERE id=%s",
                (job_id,),
            )
        else:
            backoff_seconds = min(300, 2 ** (row[0] if row else 1))
            cur.execute(
                "UPDATE extraction_jobs SET status='retry', "
                "available_at=now() + make_interval(secs => %s), updated_at=now() WHERE id=%s",
                (backoff_seconds, job_id),
            )
        conn.commit()


def enqueue_extraction_job(
    conn,
    review_id: int,
    model_key: str = "gpt-4.1-mini",
    job_type: str = "complaint_extract",
    priority: int = 100,
    commit: bool = True,
) -> None:
    """Create an extraction job for a review (idempotent via idempotency_key)."""
    idempotency = make_idempotency_key(review_id, PROMPT_VERSION)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO extraction_jobs
              (review_id, job_type, model_key, priority, idempotency_key, prompt_version)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            """,
            (review_id, job_type, model_key, priority, idempotency, PROMPT_VERSION),
        )
    if commit:
        conn.commit()


# ─── Stage handlers for queue_jobs ─────────────────────────────────────────


def _handlers() -> dict[str, Callable[[object, dict], dict]]:
    from .cluster import handle_cluster
    from .embed import handle_embed
    from .ingest import handle_ingest
    from .normalize import handle_normalize
    from .score import handle_score

    return {
        "ingest": handle_ingest,
        "normalize": handle_normalize,
        "embed": handle_embed,
        "cluster": handle_cluster,
        "score": handle_score,
    }


# ─── Worker Loops ──────────────────────────────────────────────────────────


def run_extraction_batch(
    conn,
    model_url: str,
    api_key: str,
    model_key: str,
    batch_size: int,
    rate_limiter: TokenBucket,
    monitor: ExtractionMonitor,
) -> int:
    """Claim and process one batch of extraction jobs. Returns jobs claimed."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM claim_extraction_jobs(%s, %s)", (f"worker-{os.getpid()}", batch_size))
        jobs = sorted(cur.fetchall(), key=lambda j: j["id"])
    conn.commit()
    if not jobs:
        return 0

    logger.info("Claimed %d extraction jobs", len(jobs))
    for job in jobs:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT combined_text, metadata_json->>'market_key' FROM normalized_reviews WHERE id=%s",
                    (job["review_id"],),
                )
                review = cur.fetchone()
            if not review:
                fail_job(conn, job["id"], "Review not found")
                continue
            text, market_key = review

            before = (monitor.tokens_input, monitor.tokens_output, monitor.cost_usd)
            started = time.monotonic()
            extractions = extract_feedback(
                review_text=text,
                model_url=model_url,
                api_key=api_key,
                model_key=job["model_key"] or model_key,
                rate_limiter=rate_limiter,
                monitor=monitor,
                raise_on_error=True,
            )
            tokens_in = monitor.tokens_input - before[0]
            tokens_out = monitor.tokens_output - before[1]
            cost = monitor.cost_usd - before[2]
            latency_ms = int((time.monotonic() - started) * 1000)

            # One row per atomic complaint; the job's usage is recorded on item 0.
            with conn.cursor() as cur:
                for index, ext in enumerate(extractions):
                    cur.execute(
                        """
                        INSERT INTO review_extractions
                          (review_id, job_id, item_index, schema_version, model_key,
                           extraction_json, token_input, token_output, cost_usd, latency_ms)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (job_id, item_index) DO NOTHING
                        """,
                        (
                            job["review_id"], job["id"], index, SCHEMA_VERSION, job["model_key"] or model_key,
                            ext.model_dump(),
                            tokens_in if index == 0 else 0,
                            tokens_out if index == 0 else 0,
                            round(cost, 6) if index == 0 else 0,
                            latency_ms if index == 0 else None,
                        ),
                    )
                cur.execute("UPDATE extraction_jobs SET status='done', updated_at=now() WHERE id=%s", (job["id"],))
            if extractions and market_key:
                enqueue_if_idle(conn, "cluster", market_key)
            conn.commit()

        except Exception as e:
            conn.rollback()
            logger.error("Job %d failed: %s", job["id"], e)
            fail_job(conn, job["id"], str(e))

    flush_monitor_to_db(monitor, conn)
    return len(jobs)


def extraction_worker(
    conn,
    model_url: str,
    api_key: str,
    model_key: str = "gpt-4.1-mini",
    batch_size: int = 50,
    poll_interval: float = 2.0,
    max_iterations: Optional[int] = None,
) -> None:
    """Main extraction worker loop.

    1. Claims jobs via SKIP LOCKED
    2. Fetches review text
    3. Calls LLM extraction (failures retry with backoff; they are never stored as "no complaints")
    4. Stores one validated row per atomic complaint
    5. Queues clustering for the review's market and updates metrics
    """
    rate_limiter = TokenBucket(rpm=500, tpm=150_000)
    monitor = ExtractionMonitor(model_key=model_key)
    iteration = 0

    while True:
        if max_iterations and iteration >= max_iterations:
            break
        iteration += 1
        if not run_extraction_batch(conn, model_url, api_key, model_key, batch_size, rate_limiter, monitor):
            logger.info("No jobs available, sleeping %ss", poll_interval)
            time.sleep(poll_interval)


def run_queue_batch(conn, queue_name: str, handler: Callable[[object, dict], dict], batch_size: int) -> int:
    """Claim and process one batch from queue_jobs. Returns jobs claimed."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM claim_queue_jobs(%s, %s)", (queue_name, batch_size))
        jobs = sorted(cur.fetchall(), key=lambda j: j["id"])
    conn.commit()
    if not jobs:
        return 0

    logger.info("Claimed %d jobs from queue '%s'", len(jobs), queue_name)
    for job in jobs:
        try:
            result = handler(conn, job["payload"])
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE queue_jobs SET status='done', result_json=%s, error_text=NULL, updated_at=now() WHERE id=%s",
                    (result, job["id"]),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Queue job %d failed: %s", job["id"], e)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attempts, max_attempts FROM queue_jobs WHERE id=%s",
                    (job["id"],),
                )
                row = cur.fetchone()
                if row and row[0] >= row[1]:
                    cur.execute(
                        "UPDATE queue_jobs SET status='failed', error_text=%s, updated_at=now() WHERE id=%s",
                        (str(e)[:2000], job["id"]),
                    )
                else:
                    backoff = min(300, 2 ** (row[0] if row else 1))
                    cur.execute(
                        "UPDATE queue_jobs SET status='retry', "
                        "available_at=now() + make_interval(secs => %s), error_text=%s, updated_at=now() WHERE id=%s",
                        (backoff, str(e)[:2000], job["id"]),
                    )
            conn.commit()
    return len(jobs)


def generic_queue_worker(
    conn,
    queue_name: str,
    handler: Callable[[object, dict], dict],
    batch_size: int = 50,
    poll_interval: float = 2.0,
    max_iterations: Optional[int] = None,
) -> None:
    """Generic worker for the queue_jobs table.

    The handler receives (conn, payload) and returns a result dict.
    Used for ingestion, normalization, embedding, clustering, and scoring.
    """
    iteration = 0
    while True:
        if max_iterations and iteration >= max_iterations:
            break
        iteration += 1
        if not run_queue_batch(conn, queue_name, handler, batch_size):
            time.sleep(poll_interval)


def drain(conn, model_url: str, api_key: str, model_key: str, batch_size: int = 50) -> dict:
    """Run every stage in pipeline order until a full pass finds no work."""
    handlers = _handlers()
    rate_limiter = TokenBucket(rpm=500, tpm=150_000)
    monitor = ExtractionMonitor(model_key=model_key)
    totals = {stage: 0 for stage in STAGES}
    while True:
        progressed = 0
        for stage in STAGES:
            if stage == "extract":
                n = run_extraction_batch(conn, model_url, api_key, model_key, batch_size, rate_limiter, monitor)
            else:
                n = run_queue_batch(conn, stage, handlers[stage], batch_size)
            totals[stage] += n
            progressed += n
        if not progressed:
            return totals


# ─── Celery Integration Example ────────────────────────────────────────────
#
# For teams using Celery + Redis as the broker, the extraction task wraps the
# same logic. This is an alternative to the Postgres-native SKIP LOCKED queue
# above — use one or the other, not both.
#
#   # celery_app.py
#   from celery import Celery
#
#   app = Celery("gap_engine", broker="redis://localhost:6379/0",
#                backend="db+postgresql://user:pass@host/db")
#   app.conf.task_routes = {
#       "gap_engine.extract": {"queue": "extract"},
#       "gap_engine.ingest":  {"queue": "ingest"},
#       "gap_engine.score":   {"queue": "score"},
#   }
#   app.conf.worker_concurrency = 4
#   app.conf.task_acks_late = True          # re-deliver on crash
#   app.conf.task_reject_on_worker_lost = True
#   app.conf.task_default_retry_delay = 30
#   app.conf.task_time_limit = 120
#
#   # tasks.py
#   from celery import shared_task
#   from gap_engine.extract import extract_feedback, ExtractionMonitor
#
#   @shared_task(bind=True, max_retries=3, default_retry_delay=30)
#   def extract_review_task(self, review_id, review_text, model_key="gpt-4.1-mini"):
#       monitor = ExtractionMonitor(model_key=model_key)
#       results = extract_feedback(
#           review_text=review_text,
#           model_url=os.environ["LLM_API_URL"],
#           api_key=os.environ["LLM_API_KEY"],
#           model_key=model_key,
#           monitor=monitor,
#       )
#       flush_monitor_to_db(monitor, get_db_connection())
#       return [r.model_dump() for r in results]
#
#   # Run:  celery -A celery_app worker -Q extract -l info --concurrency=4
#

# ─── BullMQ Integration Example (TypeScript) ──────────────────────────────
#
# For JS/TS teams, BullMQ + Redis is the equivalent. The queue setup mirrors
# the Celery config:
#
#   // queue.ts
#   import { Queue, Worker } from 'bullmq';
#   import IORedis from 'ioredis';
#
#   const connection = new IORedis('redis://localhost:6379');
#
#   const extractQueue = new Queue('extract', { connection });
#   const ingestQueue  = new Queue('ingest',  { connection });
#   const scoreQueue   = new Queue('score',   { connection });
#
#   // Enqueue:
#   await extractQueue.add('extract-review', { reviewId: 42 }, {
#     jobId: `review-${42}`,           // idempotency
#     attempts: 3,
#     backoff: { type: 'exponential', delay: 2000 },
#   });
#
#   // Worker:
#   new Worker('extract', async (job) => {
#     const { reviewId } = job.data;
#     // ... fetch review, call LLM, validate, store ...
#   }, { connection, concurrency: 4 });
#


# ─── CLI ───────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Gap Discovery Engine queue worker")
    parser.add_argument("--queue", required=True, choices=STAGES + ["all"])
    parser.add_argument("--model", default=os.environ.get("EXTRACT_MODEL", "gpt-4.1-mini"))
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-iterations", type=int, default=None)
    parser.add_argument("--stale-minutes", type=int, default=15,
                        help="Return jobs stuck in 'running' this long to 'retry' at startup")
    parser.add_argument("--drain", action="store_true",
                        help="With --queue all: run every stage until idle, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = get_db_connection()
    recovered = requeue_stale(conn, args.stale_minutes)
    if recovered:
        logger.info("Returned %d stale running jobs to retry", recovered)
    logger.info("Starting %s worker (model=%s, batch=%d)", args.queue, args.model, args.batch_size)

    model_url = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
    api_key = os.environ.get("LLM_API_KEY", "")

    if args.queue in ("extract", "all") and not api_key:
        parser.error("LLM_API_KEY is not set")

    if args.queue == "all":
        if args.drain:
            logger.info("Drained: %s", drain(conn, model_url, api_key, args.model, args.batch_size))
            return
        while True:
            if not any(drain(conn, model_url, api_key, args.model, args.batch_size).values()):
                time.sleep(args.poll_interval)

    if args.queue == "extract":
        extraction_worker(
            conn=conn,
            model_url=model_url,
            api_key=api_key,
            model_key=args.model,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval,
            max_iterations=args.max_iterations,
        )
    else:
        generic_queue_worker(
            conn=conn,
            queue_name=args.queue,
            handler=_handlers()[args.queue],
            batch_size=args.batch_size,
            poll_interval=args.poll_interval,
            max_iterations=args.max_iterations,
        )


if __name__ == "__main__":
    main()
