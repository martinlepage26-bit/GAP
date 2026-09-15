"""Structured LLM review extraction with cost, token, and rate-limit monitoring.

This module is the extraction stage of the gap discovery pipeline. It:
1. Reads review text from the database (or receives it directly)
2. Sends it to an LLM with a structured-output prompt
3. Validates the response against Pydantic schemas
4. Records token usage, cost, latency, and rate-limit events
5. Stores validated extractions in review_extractions
6. Updates extraction_metrics for monitoring

Usage as a script:
    python -m gap_engine.extract --review-id 42

Usage as a module:
    from gap_engine.extract import extract_feedback, ExtractionMonitor
"""

from __future__ import annotations

import json
import logging
import os
import time
import hashlib
from dataclasses import dataclass, field
from typing import Optional

import requests
from pydantic import ValidationError

from .models import (
    Extraction,
    ExtractionResult,
    validate_extraction_list,
)

logger = logging.getLogger(__name__)

# ─── Prompt ────────────────────────────────────────────────────────────────

PROMPT = """\
You extract unmet-need evidence from customer feedback.

Return JSON only with this schema:
{
  "items": [
    {
      "complaint_text": "Canonical atomic complaint statement",
      "suggestion_text": "Explicit or inferred improvement request, or null",
      "category": "feature_gap|price_gap|segment_gap|geography_language_gap|quality_gap|other",
      "segment": "Affected buyer segment, or null",
      "workflow": "Workflow or job-to-be-done context, or null",
      "severity": 1,
      "has_workaround": true,
      "willingness_to_pay": 0,
      "incumbent_named": "Competitor name, or null",
      "pain_signals": [{"type": "time_loss", "evidence": "short quote"}],
      "source_quote": "exact short verbatim quote"
    }
  ]
}

Rules:
- Extract only atomic, evidence-grounded complaints. One issue per item.
- Prefer canonical rewrites over copy-paste. Keep source_quote verbatim and short.
- severity: 1=preference, 2=annoyance, 3=recurring frustration, 4=major time/money loss, 5=compliance risk/abandonment.
- willingness_to_pay: 0=none, 1=weak desire, 2=current paid workaround, 3=explicit "I would pay/switch".
- pain_signals.type: time_loss, money_loss, risk, stress, compliance, quality_failure, workaround.
- If no real complaint exists, return {"items":[]}.
"""

PROMPT_VERSION = "v1.2.0"
SCHEMA_VERSION = "v1.0.0"

# ─── Cost Table ────────────────────────────────────────────────────────────
# Per-million-token pricing for models we support. Update as providers change.
COST_TABLE = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 6.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
}


def compute_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    """Compute USD cost for a single API call."""
    rates = COST_TABLE.get(model_key, {"input": 1.00, "output": 3.00})
    return (tokens_in / 1_000_000) * rates["input"] + (tokens_out / 1_000_000) * rates["output"]


# ─── Rate Limiter ──────────────────────────────────────────────────────────


@dataclass
class TokenBucket:
    """Simple in-process rate limiter for requests-per-minute and tokens-per-minute.

    For multi-process deployments, use a shared store (Redis) instead.
    """

    rpm: int = 500
    tpm: int = 150_000
    _req_tokens: float = field(init=False)
    _tok_tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self) -> None:
        self._req_tokens = float(self.rpm)
        self._tok_tokens = float(self.tpm)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._req_tokens = min(self.rpm, self._req_tokens + elapsed * (self.rpm / 60.0))
        self._tok_tokens = min(self.tpm, self._tok_tokens + elapsed * (self.tpm / 60.0))
        self._last_refill = now

    def acquire(self, est_tokens: int) -> bool:
        """Try to acquire one request and est_tokens token budget. Returns True if allowed."""
        self._refill()
        if self._req_tokens >= 1 and self._tok_tokens >= est_tokens:
            self._req_tokens -= 1
            self._tok_tokens -= est_tokens
            return True
        return False

    def wait_and_acquire(self, est_tokens: int, max_wait: float = 30.0) -> bool:
        """Block until budget is available or max_wait is exceeded."""
        deadline = time.monotonic() + max_wait
        while time.monotonic() < deadline:
            if self.acquire(est_tokens):
                return True
            time.sleep(0.2)
        return False


# ─── Monitor ───────────────────────────────────────────────────────────────


@dataclass
class ExtractionMonitor:
    """Accumulates metrics during an extraction batch for DB persistence.

    Call record() after each API call, then flush() to write to extraction_metrics.
    """

    model_key: str
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    validation_failures: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    rate_limit_hits: int = 0
    total_latency_ms: int = 0
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
        self.total_latency_ms += latency_ms

    @property
    def avg_latency_ms(self) -> int:
        return int(self.total_latency_ms / self.total_requests) if self.total_requests else 0

    @property
    def cost_per_extraction(self) -> float:
        return self.cost_usd / self.successful if self.successful else 0.0

    def summary(self) -> dict:
        return {
            "model": self.model_key,
            "requests": self.total_requests,
            "successful": self.successful,
            "failed": self.failed,
            "validation_failures": self.validation_failures,
            "tokens_in": self.tokens_input,
            "tokens_out": self.tokens_output,
            "cost_usd": round(self.cost_usd, 6),
            "cost_per_extraction": round(self.cost_per_extraction, 6),
            "rate_limit_hits": self.rate_limit_hits,
            "avg_latency_ms": self.avg_latency_ms,
            "retries": self.retries,
        }


# ─── Core Extraction ───────────────────────────────────────────────────────


def extract_feedback(
    review_text: str,
    model_url: str,
    api_key: str,
    model_key: str = "gpt-4.1-mini",
    rate_limiter: Optional[TokenBucket] = None,
    monitor: Optional[ExtractionMonitor] = None,
    max_retries: int = 2,
) -> list[Extraction]:
    """Send review text to an LLM and return validated Extraction objects.

    Handles:
    - Rate limiting via TokenBucket
    - Exponential backoff on 429/5xx
    - Pydantic schema validation
    - Token/cost/latency monitoring

    Returns a list of validated Extraction objects (may be empty).
    """
    est_tokens = max(200, len(review_text) // 4 + 500)

    if rate_limiter:
        if not rate_limiter.wait_and_acquire(est_tokens):
            if monitor:
                monitor.record(False, rate_limited=True)
            logger.warning("Rate limit exceeded, skipping extraction")
            return []

    payload = {
        "model": model_key,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": review_text[:12_000]},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for attempt in range(max_retries + 1):
        start = time.monotonic()
        try:
            r = requests.post(model_url, headers=headers, json=payload, timeout=60)
            latency_ms = int((time.monotonic() - start) * 1000)

            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                logger.warning("429 rate limited, waiting %ss", retry_after)
                if monitor:
                    monitor.record(False, rate_limited=True, retried=attempt > 0)
                if attempt < max_retries:
                    time.sleep(retry_after)
                    continue
                return []

            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            cost = compute_cost(model_key, tokens_in, tokens_out)

            parsed = json.loads(content)
            result, errors = validate_extraction_list(parsed)

            if errors:
                logger.error("Schema validation failed: %s", errors)
                if monitor:
                    monitor.record(
                        False,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cost=cost,
                        latency_ms=latency_ms,
                        validation_failed=True,
                    )
                return []

            if monitor:
                monitor.record(
                    True,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost=cost,
                    latency_ms=latency_ms,
                )

            return result.items if result else []

        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.error("Extraction error (attempt %d): %s", attempt + 1, e)
            if attempt < max_retries:
                backoff = 2 ** attempt + 1
                time.sleep(backoff)
                if monitor:
                    monitor.record(False, latency_ms=latency_ms, retried=True)
                continue
            if monitor:
                monitor.record(False, latency_ms=latency_ms)
            return []

    return []


# ─── DB Integration ────────────────────────────────────────────────────────


def make_idempotency_key(review_id: int, prompt_version: str) -> str:
    """Generate a deterministic idempotency key for a review extraction job."""
    raw = f"{review_id}:{prompt_version}:{PROMPT_VERSION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def flush_monitor_to_db(monitor: ExtractionMonitor, db_conn) -> None:
    """Write accumulated monitor metrics to extraction_metrics via upsert function."""
    db_conn.execute(
        "SELECT upsert_extraction_metrics(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            monitor.model_key,
            monitor.successful,
            monitor.failed,
            monitor.validation_failures,
            monitor.tokens_input,
            monitor.tokens_output,
            monitor.cost_usd,
            monitor.rate_limit_hits,
            monitor.avg_latency_ms,
            monitor.retries,
        ),
    )
