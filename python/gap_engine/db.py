"""Database connection and queue helpers shared by every worker.

Workers connect with the Supabase service-role connection string
(DATABASE_URL or SUPABASE_DB_URL). The browser key never reaches this code.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional, Sequence

import psycopg2
import psycopg2.extensions
from psycopg2.extras import Json

# Python dicts are written as jsonb wherever they appear as query parameters.
psycopg2.extensions.register_adapter(dict, Json)


def get_db_connection():
    """Create a psycopg2 connection from environment variables."""
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        raise RuntimeError("Set DATABASE_URL (Supabase service-role Postgres URL)")
    return psycopg2.connect(dsn)


def vector_literal(values: Sequence[float]) -> str:
    """pgvector text form: [0.1,0.2,...]."""
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def parse_vector(text: str) -> list[float]:
    """Parse pgvector text output back into floats."""
    text = (text or "").strip().strip("[]")
    return [float(x) for x in text.split(",")] if text else []


def enqueue_job(
    conn,
    queue_name: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
    priority: int = 100,
) -> bool:
    """Insert a queue_jobs row. Returns False when the idempotency key already exists.

    Does not commit; the caller commits with its own unit of work.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO queue_jobs (queue_name, payload, priority, idempotency_key)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (queue_name, payload, priority, idempotency_key),
        )
        return cur.fetchone() is not None


def enqueue_if_idle(conn, queue_name: str, market_key: str, priority: int = 100) -> bool:
    """Queue one market-wide job (cluster, score) unless one is already waiting.

    A job that is already running does not count: work that arrived after it
    started still needs a fresh pass.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"enqueue:{queue_name}:{market_key}",))
        cur.execute(
            """
            SELECT 1 FROM queue_jobs
            WHERE queue_name = %s AND status IN ('queued','retry')
              AND payload->>'market_key' = %s
            LIMIT 1
            """,
            (queue_name, market_key),
        )
        if cur.fetchone():
            return False
    return enqueue_job(conn, queue_name, {"market_key": market_key}, priority=priority)


def requeue_stale(conn, minutes: int = 15) -> int:
    """Return jobs left 'running' by a crashed worker to the retry state."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE queue_jobs SET status = 'retry', available_at = now(), updated_at = now()
            WHERE status = 'running' AND updated_at < now() - make_interval(mins => %s)
            """,
            (minutes,),
        )
        n = cur.rowcount
        cur.execute(
            """
            UPDATE extraction_jobs SET status = 'retry', available_at = now(), updated_at = now()
            WHERE status = 'running' AND updated_at < now() - make_interval(mins => %s)
            """,
            (minutes,),
        )
        n += cur.rowcount
    conn.commit()
    return n


def stable_key(*parts: object) -> str:
    """Deterministic idempotency key from arbitrary parts."""
    raw = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]
