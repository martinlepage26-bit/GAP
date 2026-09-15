"""Extraction cost, token usage, and rate-limit monitoring.

This module provides:
1. A monitor class that accumulates metrics during extraction batches
2. Functions to query historical metrics from extraction_metrics table
3. Drift detection: schema validation failure rates, cost-per-extraction trends,
   token usage anomalies, rate-limit event tracking
4. Alerting helpers that log warnings when thresholds are breached

Usage:
    from gap_engine.monitor import MetricsCollector, check_drift

    # In a worker loop:
    collector = MetricsCollector(model_key="gpt-4.1-mini")
    collector.record(success=True, tokens_in=1200, tokens_out=80, cost=0.001, latency_ms=800)
    collector.flush(db_conn)

    # Periodic drift check:
    alerts = check_drift(db_conn, model_key="gpt-4.1-mini")
    for a in alerts:
        logger.warning(a)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ─── Alert Thresholds ──────────────────────────────────────────────────────

ALERT_VALIDATION_FAILURE_RATE = 0.05      # > 5% schema validation failures
ALERT_COST_PER_EXTRACTION_USD = 0.005     # > $0.005 per successful extraction
ALERT_RATE_LIMIT_HITS_DAILY = 10          # > 10 rate-limit events per day
ALERT_AVG_LATENCY_MS = 5000               # > 5s average latency
ALERT_TOKEN_USAGE_SPIKE_MULTIPLIER = 2.0  # 2x day-over-day token usage


# ─── Metrics Collector ────────────────────────────────────────────────────


@dataclass
class MetricsCollector:
    """Accumulates per-batch extraction metrics for DB persistence.

    Distinct from ExtractionMonitor in extract.py — this collector focuses on
    drift-detectable aggregates and is designed to be flushed once per worker
    batch cycle.
    """

    model_key: str
    run_date: date = field(default_factory=date.today)
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    validation_failures: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    rate_limit_hits: int = 0
    latency_samples: list[int] = field(default_factory=list)
    retries: int = 0

    def record(
        self,
        success: bool,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost: float = 0.0,
        latency_ms: int = 0,
        validation_failed: bool = False,
        rate_limited: bool = False,
        retried: bool = False,
    ) -> None:
        self.total_requests += 1
        if success:
            self.successful += 1
        else:
            self.failed += 1
        if validation_failed:
            self.validation_failures += 1
        if rate_limited:
            self.rate_limit_hits += 1
        if retried:
            self.retries += 1
        self.tokens_input += tokens_in
        self.tokens_output += tokens_out
        self.cost_usd += cost
        if latency_ms > 0:
            self.latency_samples.append(latency_ms)

    @property
    def avg_latency_ms(self) -> int:
        return int(sum(self.latency_samples) / len(self.latency_samples)) if self.latency_samples else 0

    @property
    def cost_per_extraction(self) -> float:
        return self.cost_usd / self.successful if self.successful else 0.0

    @property
    def validation_failure_rate(self) -> float:
        return self.validation_failures / self.total_requests if self.total_requests else 0.0

    def summary(self) -> dict:
        return {
            "model": self.model_key,
            "date": self.run_date.isoformat(),
            "requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "validation_failure_rate": round(self.validation_failure_rate, 4),
            "tokens_in": self.tokens_input,
            "tokens_out": self.tokens_output,
            "total_cost_usd": round(self.cost_usd, 6),
            "cost_per_extraction": round(self.cost_per_extraction, 6),
            "rate_limit_hits": self.rate_limit_hits,
            "avg_latency_ms": self.avg_latency_ms,
            "retries": self.retries,
        }

    def flush(self, conn: psycopg2.extensions.connection) -> None:
        """Write accumulated metrics to extraction_metrics via upsert function."""
        with conn.cursor() as cur:
            cur.execute(
                "SELECT upsert_extraction_metrics(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    self.model_key,
                    self.successful,
                    self.failed,
                    self.validation_failures,
                    self.tokens_input,
                    self.tokens_output,
                    self.cost_usd,
                    self.rate_limit_hits,
                    self.avg_latency_ms,
                    self.retries,
                ),
            )
        conn.commit()
        logger.info("Flushed metrics: %s", self.summary())


# ─── Historical Queries ────────────────────────────────────────────────────


def get_daily_metrics(conn, model_key: Optional[str] = None, days: int = 30) -> list[dict]:
    """Retrieve daily extraction metrics for the last N days."""
    query = """
        SELECT run_date, model_key, total_requests, successful_extractions,
               failed_extractions, validation_failures, total_tokens_input,
               total_tokens_output, total_cost_usd, rate_limit_hits,
               avg_latency_ms, retry_count
        FROM extraction_metrics
        WHERE run_date >= CURRENT_DATE - %s
    """
    params: list = [days]
    if model_key:
        query += " AND model_key = %s"
        params.append(model_key)
    query += " ORDER BY run_date DESC, model_key"

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def get_cost_summary(conn, days: int = 7) -> dict:
    """Aggregate cost and token usage across all models for the last N days."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
              model_key,
              SUM(total_requests) as total_requests,
              SUM(successful_extractions) as successful,
              SUM(total_tokens_input) as tokens_in,
              SUM(total_tokens_output) as tokens_out,
              SUM(total_cost_usd) as total_cost,
              SUM(rate_limit_hits) as rate_limit_hits,
              AVG(avg_latency_ms) as avg_latency
            FROM extraction_metrics
            WHERE run_date >= CURRENT_DATE - %s
            GROUP BY model_key
            ORDER BY total_cost DESC
            """,
            (days,),
        )
        rows = cur.fetchall()

    return {
        "period_days": days,
        "models": [dict(r) for r in rows],
        "grand_total_cost": sum(float(r["total_cost"] or 0) for r in rows),
        "grand_total_tokens_in": sum(int(r["tokens_in"] or 0) for r in rows),
        "grand_total_tokens_out": sum(int(r["tokens_out"] or 0) for r in rows),
        "grand_total_requests": sum(int(r["total_requests"] or 0) for r in rows),
    }


# ─── Drift Detection ──────────────────────────────────────────────────────


def check_drift(
    conn: psycopg2.extensions.connection,
    model_key: Optional[str] = None,
) -> list[str]:
    """Check for data/model drift and return a list of alert messages.

    Checks:
    1. Schema validation failure rate above threshold
    2. Cost per extraction above threshold
    3. Rate-limit hits above daily threshold
    4. Average latency above threshold
    5. Token usage spike (day-over-day multiplier)
    """
    alerts: list[str] = []
    today = date.today()
    yesterday = today - timedelta(days=1)

    model_filter = "AND model_key = %s" if model_key else ""
    params: list = []
    if model_key:
        params.append(model_key)

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 1. Validation failure rate
        cur.execute(
            f"""
            SELECT
              SUM(total_requests) as reqs,
              SUM(validation_failures) as vfails
            FROM extraction_metrics
            WHERE run_date >= CURRENT_DATE - 7
            {model_filter}
            """,
            params,
        )
        row = cur.fetchone()
        if row and row["reqs"] and row["reqs"] > 0:
            vfail_rate = float(row["vfails"] or 0) / float(row["reqs"])
            if vfail_rate > ALERT_VALIDATION_FAILURE_RATE:
                alerts.append(
                    f"VALIDATION DRIFT: schema validation failure rate is "
                    f"{vfail_rate:.1%} (threshold {ALERT_VALIDATION_FAILURE_RATE:.1%}) "
                    f"over last 7 days"
                )

        # 2. Cost per extraction
        cur.execute(
            f"""
            SELECT
              SUM(total_cost_usd) as cost,
              SUM(successful_extractions) as success
            FROM extraction_metrics
            WHERE run_date = %s
            {model_filter}
            """,
            [today] + params,
        )
        row = cur.fetchone()
        if row and row["success"] and row["success"] > 0:
            cost_per = float(row["cost"] or 0) / float(row["success"])
            if cost_per > ALERT_COST_PER_EXTRACTION_USD:
                alerts.append(
                    f"COST ALERT: cost per extraction is ${cost_per:.4f} "
                    f"(threshold ${ALERT_COST_PER_EXTRACTION_USD:.4f})"
                )

        # 3. Rate limit hits
        cur.execute(
            f"""
            SELECT SUM(rate_limit_hits) as hits
            FROM extraction_metrics
            WHERE run_date = %s
            {model_filter}
            """,
            [today] + params,
        )
        row = cur.fetchone()
        if row and row["hits"] and int(row["hits"]) > ALERT_RATE_LIMIT_HITS_DAILY:
            alerts.append(
                f"RATE LIMIT ALERT: {row['hits']} rate-limit events today "
                f"(threshold {ALERT_RATE_LIMIT_HITS_DAILY})"
            )

        # 4. Average latency
        cur.execute(
            f"""
            SELECT AVG(avg_latency_ms) as avg_lat
            FROM extraction_metrics
            WHERE run_date >= CURRENT_DATE - 3
            {model_filter}
            """,
            params,
        )
        row = cur.fetchone()
        if row and row["avg_lat"] and int(row["avg_lat"]) > ALERT_AVG_LATENCY_MS:
            alerts.append(
                f"LATENCY DRIFT: average latency is {int(row['avg_lat'])}ms "
                f"(threshold {ALERT_AVG_LATENCY_MS}ms) over last 3 days"
            )

        # 5. Token usage spike
        cur.execute(
            f"""
            SELECT
              (SELECT SUM(total_tokens_input + total_tokens_output)
               FROM extraction_metrics WHERE run_date = %s {model_filter}) as today_tokens,
              (SELECT SUM(total_tokens_input + total_tokens_output)
               FROM extraction_metrics WHERE run_date = %s {model_filter}) as yest_tokens
            """,
            [today] + params + [yesterday] + params,
        )
        row = cur.fetchone()
        if row and row["today_tokens"] and row["yest_tokens"] and row["yest_tokens"] > 0:
            ratio = float(row["today_tokens"]) / float(row["yest_tokens"])
            if ratio > ALERT_TOKEN_USAGE_SPIKE_MULTIPLIER:
                alerts.append(
                    f"TOKEN SPIKE: today's token usage is {ratio:.1f}x "
                    f"yesterday's ({row['today_tokens']} vs {row['yest_tokens']})"
                )

    return alerts


def get_queue_depth(conn) -> dict:
    """Get queue depth by status for monitoring dashboard."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT job_type, status, COUNT(*) as count
            FROM extraction_jobs
            GROUP BY job_type, status
            ORDER BY job_type, status
            """
        )
        extraction_counts = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """
            SELECT queue_name, status, COUNT(*) as count
            FROM queue_jobs
            GROUP BY queue_name, status
            ORDER BY queue_name, status
            """
        )
        queue_counts = [dict(r) for r in cur.fetchall()]

    return {
        "extraction_jobs": extraction_counts,
        "queue_jobs": queue_counts,
    }
