"""Semantic deduplication using pgvector nearest-neighbor queries.

This module implements the four-stage dedup pipeline:
1. Exact URL dedupe (source + external_id)
2. Near-exact text dedupe (content_hash)
3. Thread-aware dedupe (quote chains, mirror posts)
4. Semantic dedupe (pgvector embeddings + cosine similarity)

Stage 4 uses the find_semantic_duplicates() SQL function defined in the
migration, which applies blocking filters (same language, same competitor,
30-day window) before vector search to keep candidate sets small.

Usage:
    from gap_engine.dedup import SemanticDeduplicator

    dedup = SemanticDeduplicator(db_conn)
    candidates = dedup.find_duplicates(review_id=42)

    # Group high-confidence duplicates
    dedup.group_duplicates(review_id=42, threshold=0.98)
"""

from __future__ import annotations

import hashlib
import re
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ─── Thresholds ────────────────────────────────────────────────────────────

THRESHOLD_EXACT = 0.98       # likely exact or templated duplicate
THRESHOLD_NEAR = 0.93        # likely near-duplicate; auto-group if lexical overlap high
THRESHOLD_REVIEW = 0.88      # analyst review or secondary verifier
THRESHOLD_REJECT = 0.88      # below this = related but not duplicate

# ─── Text Normalization ────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip URLs — for exact dedupe hashing."""
    text = (text or "").lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"http\S+", "", text)
    return text.strip()


def canonicalize_url(url: str) -> str:
    """Strip tracking params and trailing slash for stable URL dedupe."""
    if not url:
        return ""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def content_hash(title: str, body: str) -> bytes:
    """SHA-256 of normalized title + body for exact text dedupe."""
    text = normalize_text((title or "") + "\n" + (body or ""))
    return hashlib.sha256(text.encode("utf-8")).digest()


# ─── Semantic Deduplicator ─────────────────────────────────────────────────


@dataclass
class DuplicateCandidate:
    """A candidate duplicate review found by semantic search."""

    review_id: int
    cosine_similarity: float
    combined_text: str
    competitor_name: Optional[str]
    product_name: Optional[str]

    @property
    def confidence_tier(self) -> str:
        if self.cosine_similarity >= THRESHOLD_EXACT:
            return "exact"
        elif self.cosine_similarity >= THRESHOLD_NEAR:
            return "near"
        elif self.cosine_similarity >= THRESHOLD_REVIEW:
            return "review"
        else:
            return "reject"


class SemanticDeduplicator:
    """Runs the pgvector-based semantic dedup pipeline against the database.

    Requires that review_embeddings already exist for the reviews being compared.
    Use the embedding worker to generate them before running dedup.
    """

    def __init__(self, conn: psycopg2.extensions.connection):
        self.conn = conn

    def find_duplicates(
        self,
        review_id: int,
        threshold: float = THRESHOLD_REVIEW,
        limit: int = 20,
    ) -> list[DuplicateCandidate]:
        """Find semantic duplicate candidates for a single review.

        Uses find_semantic_duplicates() SQL function which applies:
        - Same language blocking
        - Same competitor_name blocking
        - 30-day time window blocking
        - HNSW vector index for fast ANN retrieval
        """
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM find_semantic_duplicates(%s, %s, %s)",
                (review_id, threshold, limit),
            )
            rows = cur.fetchall()

        return [
            DuplicateCandidate(
                review_id=row["candidate_review_id"],
                cosine_similarity=row["cosine_similarity"],
                combined_text=row["combined_text"],
                competitor_name=row["competitor_name"],
                product_name=row["product_name"],
            )
            for row in rows
        ]

    def group_duplicates(
        self,
        review_id: int,
        threshold: float = THRESHOLD_EXACT,
        auto_group_below: float = THRESHOLD_NEAR,
    ) -> Optional[int]:
        """Find and group duplicates for a review into duplicate_groups.

        - Candidates >= threshold: auto-merge into a group
        - Candidates >= auto_group_below: auto-group but keep originals
        - Candidates below auto_group_below: skip (analyst review queue)

        Returns the duplicate_groups.id if a group was created, None otherwise.
        """
        candidates = self.find_duplicates(review_id, threshold=auto_group_below)

        high_conf = [c for c in candidates if c.cosine_similarity >= threshold]
        medium_conf = [c for c in candidates if auto_group_below <= c.cosine_similarity < threshold]

        if not high_conf and not medium_conf:
            return None

        # Create group — prefer highest-confidence candidate as canonical
        all_conf = sorted(high_conf + medium_conf, key=lambda c: -c.cosine_similarity)
        canonical_id = review_id  # keep the original as canonical by default

        with self.conn.cursor() as cur:
            group_type = "exact" if high_conf else "semantic"
            confidence = all_conf[0].cosine_similarity

            cur.execute(
                """
                INSERT INTO duplicate_groups (group_type, canonical_review_id, confidence)
                VALUES (%s, %s, %s) RETURNING id
                """,
                (group_type, canonical_id, confidence),
            )
            group_id = cur.fetchone()[0]

            # Add the original review as a member
            cur.execute(
                """
                INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json)
                VALUES (%s, %s, 1.0, %s::jsonb)
                """,
                (group_id, review_id, '{"reason":"original"}'),
            )

            # Add all candidates as members
            for cand in all_conf:
                reason = {
                    "reason": cand.confidence_tier,
                    "cosine_sim": round(cand.cosine_similarity, 5),
                }
                cur.execute(
                    """
                    INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json)
                    VALUES (%s, %s, %s, %s::jsonb)
                    ON CONFLICT DO NOTHING
                    """,
                    (group_id, cand.review_id, cand.cosine_similarity, reason),
                )

            self.conn.commit()

        logger.info(
            "Created duplicate group %d for review %d (%d high, %d medium confidence)",
            group_id, review_id, len(high_conf), len(medium_conf),
        )
        return group_id

    def dedupe_batch(self, review_ids: list[int]) -> dict:
        """Run semantic dedup across a batch of reviews.

        Returns summary stats: groups created, total candidates, by tier.
        """
        groups_created = 0
        total_candidates = 0
        by_tier = {"exact": 0, "near": 0, "review": 0, "reject": 0}

        for rid in review_ids:
            candidates = self.find_duplicates(rid)
            total_candidates += len(candidates)
            for c in candidates:
                by_tier[c.confidence_tier] += 1

            gid = self.group_duplicates(rid)
            if gid:
                groups_created += 1

        return {
            "reviews_checked": len(review_ids),
            "groups_created": groups_created,
            "total_candidates": total_candidates,
            "by_tier": by_tier,
        }

    def find_exact_text_duplicates(self, content_hash_bytes: bytes) -> list[int]:
        """Find reviews with the same exact dedupe hash (stage 2 dedupe)."""
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM normalized_reviews WHERE exact_dedupe_hash = %s",
                (content_hash_bytes,),
            )
            return [row[0] for row in cur.fetchall()]
