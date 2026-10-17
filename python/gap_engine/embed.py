"""Embedding stage: review_embeddings + semantic dedup, then queue extraction.

Embeddings are 384-dimensional to match the schema. The default model runs
locally through fastembed, so review text is not sent to a third party for
this step, and it is multilingual (French and English share one space).

    EMBED_PROVIDER=fastembed   (default)
    EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
    EMBED_PROVIDER=hash        deterministic bag-of-words vectors; offline tests only

A review that is a near-duplicate (cosine >= 0.93) of an already-embedded
review in the same market joins that review's semantic duplicate group and is not sent to
the LLM. Everything else gets one extraction job.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Optional, Sequence

from .db import vector_literal
from .dedup import THRESHOLD_NEAR, SemanticDeduplicator

logger = logging.getLogger(__name__)

DIM = 384
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder:
    model_key: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class FastEmbedder(Embedder):
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding  # optional dependency

        self.model_key = model_name
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [list(map(float, v)) for v in self._model.embed(list(texts))]
        for v in vectors:
            if len(v) != DIM:
                raise ValueError(f"{self.model_key} returned {len(v)} dims; schema needs {DIM}")
        return vectors


class HashEmbedder(Embedder):
    """Signed feature hashing of word unigrams and bigrams, L2-normalized."""

    model_key = "hash-bow-384"
    _word = re.compile(r"\w+", re.UNICODE)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            words = self._word.findall(text.lower())
            v = [0.0] * DIM
            for tok in words + [f"{a} {b}" for a, b in zip(words, words[1:])]:
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
                v[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        provider = os.environ.get("EMBED_PROVIDER", "fastembed")
        if provider == "hash":
            _embedder = HashEmbedder()
        elif provider == "fastembed":
            _embedder = FastEmbedder(os.environ.get("EMBED_MODEL", DEFAULT_MODEL))
        else:
            raise ValueError(f"Unknown EMBED_PROVIDER {provider!r}")
    return _embedder


def handle_embed(conn, job: dict) -> dict:
    from .worker import enqueue_extraction_job  # avoid import cycle

    review_id = int(job["review_id"])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT combined_text, metadata_json->>'market_key' FROM normalized_reviews WHERE id = %s",
            (review_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"skipped": "review not found"}
    text, market_key = row

    embedder = get_embedder()
    [vector] = embedder.embed([text])
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO review_embeddings (review_id, model_key, embedding)
            VALUES (%s, %s, %s::vector)
            ON CONFLICT (review_id) DO UPDATE SET model_key = EXCLUDED.model_key,
              embedding = EXCLUDED.embedding, created_at = now()
            """,
            (review_id, embedder.model_key, vector_literal(vector)),
        )

    # Any candidate already has an embedding, so it was processed first and
    # already holds the extraction (or its own duplicate group).
    earlier = SemanticDeduplicator(conn).find_duplicates(review_id, threshold=THRESHOLD_NEAR)
    if earlier:
        ids = [c.review_id for c in earlier]
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM normalized_reviews WHERE id = ANY(%s) AND metadata_json->>'market_key' IS NOT DISTINCT FROM %s",
                (ids, market_key),
            )
            same_market = {r[0] for r in cur.fetchall()}
        earlier = [c for c in earlier if c.review_id in same_market]

    if earlier:
        best = max(earlier, key=lambda c: c.cosine_similarity)
        group_id = _join_semantic_group(conn, best.review_id, review_id, best.cosine_similarity)
        conn.commit()
        return {"review_id": review_id, "semantic_duplicate_group": group_id}

    enqueue_extraction_job(conn, review_id, model_key=os.environ.get("EXTRACT_MODEL", "gpt-4.1-mini"), commit=False)
    conn.commit()
    return {"review_id": review_id, "queued_extraction": True}


def _join_semantic_group(conn, canonical_id: int, review_id: int, similarity: float) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id FROM duplicate_groups g
            JOIN duplicate_group_members m ON m.duplicate_group_id = g.id
            WHERE m.review_id = %s ORDER BY g.id LIMIT 1
            """,
            (canonical_id,),
        )
        row = cur.fetchone()
        if row:
            group_id = row[0]
        else:
            cur.execute(
                "INSERT INTO duplicate_groups (group_type, canonical_review_id, confidence) VALUES ('semantic', %s, %s) RETURNING id",
                (canonical_id, round(similarity, 3)),
            )
            group_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json) VALUES (%s, %s, 1.0, %s)",
                (group_id, canonical_id, {"reason": "original"}),
            )
        cur.execute(
            """
            INSERT INTO duplicate_group_members (duplicate_group_id, review_id, match_score, reason_json)
            VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (group_id, review_id, round(similarity, 5), {"reason": "semantic", "cosine_sim": round(similarity, 5)}),
        )
    return group_id
