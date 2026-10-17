"""Normalization stage: raw_documents -> normalized_reviews.

Strips HTML, collapses whitespace, detects English/French, carries the
market and competitor from ingestion into metadata_json, and catches exact
duplicates (same normalized text from the same market) before any embedding
or LLM spend. Each kept review queues one `embed` job.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Optional

from .db import enqueue_job, stable_key
from .dedup import content_hash

logger = logging.getLogger(__name__)

MIN_BODY_CHARS = 20

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-zàâçéèêëîïôûùüÿœæ']+")

_FR = {"le", "la", "les", "des", "est", "et", "une", "un", "pour", "pas", "que", "qui",
       "dans", "avec", "sur", "je", "nous", "vous", "mais", "très", "ça", "du", "au", "ne"}
_EN = {"the", "and", "is", "to", "of", "it", "for", "with", "this", "that", "not", "but",
       "you", "we", "on", "are", "was", "have", "i", "my", "in", "be", "they", "can"}


def clean_text(text: Optional[str]) -> str:
    text = (text or "").replace("<p>", "\n").replace("<br>", "\n")
    text = html.unescape(_TAG.sub(" ", text))
    lines = [_WS.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def detect_language(text: str) -> Optional[str]:
    """Stopword vote between French and English; None when unclear."""
    words = _WORD.findall(text.lower())
    if len(words) < 4:
        return None
    fr = sum(w in _FR for w in words)
    en = sum(w in _EN for w in words)
    if fr == en:
        return None
    return "fr" if fr > en else "en"


def handle_normalize(conn, job: dict) -> dict:
    raw_id = int(job["raw_document_id"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT source_id, title, raw_text, raw_payload FROM raw_documents WHERE id = %s",
            (raw_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"skipped": "raw document not found"}
    source_id, title, raw_text, payload = row
    meta = (payload or {}).get("_ingest", {})

    title = clean_text(title) or None
    body = clean_text(raw_text)
    if len(body) < MIN_BODY_CHARS and not (title and len(title) >= MIN_BODY_CHARS):
        return {"skipped": "too short"}
    if not body:
        body, title = title, None

    dedupe_hash = content_hash(title or "", body)
    market_key = meta.get("market_key")
    metadata = {k: v for k, v in meta.items() if k in ("market_key", "run_id")}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO normalized_reviews
              (raw_document_id, source_id, product_name, competitor_name, rating_5,
               language, country, author_hash, title, body_text, metadata_json, exact_dedupe_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (raw_document_id) DO NOTHING
            RETURNING id
            """,
            (
                raw_id, source_id, meta.get("product"), meta.get("competitor"), meta.get("rating_5"),
                meta.get("language") or detect_language(f"{title or ''} {body}"), meta.get("country"),
                meta.get("author_hash"), title, body, metadata, dedupe_hash,
            ),
        )
        new = cur.fetchone()
        if not new:
            conn.rollback()
            return {"skipped": "already normalized"}
        review_id = new[0]

        # Exact duplicate inside the same market: group it, skip embedding and extraction.
        cur.execute(
            """
            SELECT id FROM normalized_reviews
            WHERE exact_dedupe_hash = %s AND id <> %s
              AND metadata_json->>'market_key' IS NOT DISTINCT FROM %s
            ORDER BY id LIMIT 1
            """,
            (dedupe_hash, review_id, market_key),
        )
        twin = cur.fetchone()
        if twin:
            canonical = twin[0]
            cur.execute(
                """
                SELECT g.id FROM duplicate_groups g
                WHERE g.group_type = 'exact' AND g.canonical_review_id = %s LIMIT 1
                """,
                (canonical,),
            )
            group = cur.fetchone()
            if group:
                group_id = group[0]
            else:
                cur.execute(
                    "INSERT INTO duplicate_groups (group_type, canonical_review_id, confidence) VALUES ('exact', %s, 1.0) RETURNING id",
                    (canonical,),
                )
                group_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json) VALUES (%s, %s, 1.0, %s)",
                    (group_id, canonical, {"reason": "original"}),
                )
            cur.execute(
                """
                INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json)
                VALUES (%s, %s, 1.0, %s) ON CONFLICT DO NOTHING
                """,
                (group_id, review_id, {"reason": "exact_text"}),
            )
            conn.commit()
            return {"review_id": review_id, "duplicate_of": canonical}

    enqueue_job(conn, "embed", {"review_id": review_id}, idempotency_key=stable_key("embed", review_id))
    conn.commit()
    return {"review_id": review_id}
