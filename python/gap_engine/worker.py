"""Background batch queue workers using Postgres SKIP LOCKED.

This module implements worker loops for the gap discovery pipeline. It supports
two queue patterns:

1. **Postgres-native SKIP LOCKED queue** (default, no external deps)
   - Uses claim_extraction_jobs() / claim_queue_jobs() functions
   - Workers poll the DB, claim jobs atomically, process them, and update status
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
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Optional, Callable

import psycopg2
from psycopg2.extras import RealDictCursor

from .extract import (
    extract_feedback,
    ExtractionMonitor,
    TokenBucket,
    flush_monitor_to_db,
    make_idempotency_key,
    PROMPT_VERSION,
    SCHEMA_VERSION,
)
from .models import Extraction

logger = logging.getLogger(__name__)

# ─── Database Connection ───────────────────────────────────────────────────


def get_db_connection():
    """Create a psycopg2 connection from environment variables."""
    return psycopg2.connect(os.environ.get("DATABASE_URL", os.environ.get("SUPABASE_DB_URL")))


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
                "available_at=now() + interval '%s seconds', updated_at=now() WHERE id=%s",
                (backoff_seconds, job_id),
            )
        conn.commit()


def enqueue_extraction_job(
    conn,
    review_id: int,
    model_key: str = "gpt-4.1-mini",
    job_type: str = "complaint_extract",
    priority: int = 100,
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
        conn.commit()


# ─── Worker Loops ──────────────────────────────────────────────────────────


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
    3. Calls LLM extraction
    4. Stores validated results
    5. Updates metrics
    """
    rate_limiter = TokenBucket(rpm=500, tpm=150_000)
    monitor = ExtractionMonitor(model_key=model_key)
    iteration = 0

    while True:
        if max_iterations and iteration >= max_iterations:
            break
        iteration += 1

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM claim_extraction_jobs(%s, %s)", (f"worker-{os.getpid()}", batch_size))
            jobs = cur.fetchall()

        if not jobs:
            logger.info("No jobs available, sleeping %ss", poll_interval)
            time.sleep(poll_interval)
            continue

        logger.info("Claimed %d extraction jobs", len(jobs))

        for job in jobs:
            try:
                # Fetch review text
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT combined_text FROM normalized_reviews WHERE id=%s",
                        (job["review_id"],),
                    )
                    review = cur.fetchone()

                if not review:
                    fail_job(conn, job["id"], "Review not found")
                    continue

                extractions = extract_feedback(
                    review_text=review["combined_text"],
                    model_url=model_url,
                    api_key=api_key,
                    model_key=model_key,
                    rate_limiter=rate_limiter,
                    monitor=monitor,
                )

                # Store each extraction
                for ext in extractions:
                    ext_dict = ext.model_dump()
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO review_extractions
                              (review_id, job_id, schema_version, model_key,
                               extraction_json, token_input, token_output, cost_usd, latency_ms)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                job["review_id"],
                                job["id"],
                                SCHEMA_VERSION,
                                model_key,
                                conn.adapters.adapt_jsonb(ext_dict) if hasattr(conn.adapters, "adapt_jsonb") else ext_dict,
                                monitor.tokens_input,
                                monitor.tokens_output,
                                monitor.cost_usd,
                                monitor.avg_latency_ms,
                            ),
                        )
                conn.commit()
                complete_job(conn, job["id"])

            except Exception as e:
                logger.error("Job %d failed: %s", job["id"], e)
                fail_job(conn, job["id"], str(e))

        # Flush metrics after each batch
        if monitor.total_requests > 0:
            flush_monitor_to_db(monitor, conn)
            logger.info("Metrics flushed: %s", monitor.summary())


def generic_queue_worker(
    conn,
    queue_name: str,
    handler: Callable[[dict], dict],
    batch_size: int = 50,
    poll_interval: float = 2.0,
    max_iterations: Optional[int] = None,
) -> None:
    """Generic worker for the queue_jobs table.

    The handler receives the job payload dict and returns a result dict.
    Suitable for ingestion, normalization, embedding, clustering, or scoring tasks.
    """
    iteration = 0
    while True:
        if max_iterations and iteration >= max_iterations:
            break
        iteration += 1

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM claim_queue_jobs(%s, %s)", (queue_name, batch_size))
            jobs = cur.fetchall()

        if not jobs:
            time.sleep(poll_interval)
            continue

        logger.info("Claimed %d jobs from queue '%s'", len(jobs), queue_name)

        for job in jobs:
            try:
                result = handler(job["payload"])
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE queue_jobs SET status='done', result_json=%s, updated_at=now() WHERE id=%s",
                        (result, job["id"]),
                    )
                conn.commit()
            except Exception as e:
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
                            (str(e), job["id"]),
                        )
                    else:
                        backoff = min(300, 2 ** (row[0] if row else 1))
                        cur.execute(
                            "UPDATE queue_jobs SET status='retry', "
                            "available_at=now() + interval '%s seconds', error_text=%s, updated_at=now() WHERE id=%s",
                            (backoff, str(e), job["id"]),
                        )
                conn.commit()


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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Gap engine queue worker")
    parser.add_argument("--queue", required=True, choices=["extract", "ingest", "normalize", "embed", "cluster", "score"])
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-iterations", type=int, default=None, help="Stop after N iterations (for testing)")
    args = parser.parse_args()

    conn = get_db_connection()
    logger.info("Starting %s worker (model=%s, batch=%d)", args.queue, args.model, args.batch_size)

    if args.queue == "extract":
        model_url = os.environ.get("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        api_key = os.environ.get("LLM_API_KEY", "")
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
        def noop_handler(payload: dict) -> dict:
            logger.info("Processing %s job: %s", args.queue, payload)
            return {"status": "done"}

        generic_queue_worker(
            conn=conn,
            queue_name=args.queue,
            handler=noop_handler,
            batch_size=args.batch_size,
            poll_interval=args.poll_interval,
            max_iterations=args.max_iterations,
        )


if __name__ == "__main__":
    main()
