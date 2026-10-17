"""Clustering stage: group extracted complaints into gap_clusters per market.

Incremental leader clustering, one market at a time under an advisory lock:

1. Embed any valid extraction in the market that has no extraction_embeddings row
   (complaint text + workflow, same embedder as reviews).
2. Rebuild the centroid of each active cluster from its members.
3. Walk unassigned extractions oldest first. Each joins the closest active
   cluster of the same category when cosine >= CLUSTER_THRESHOLD (default 0.78);
   otherwise it starts a new cluster.
4. Relabel touched clusters from their medoid complaint and write a short,
   evidence-count summary. Then queue a `score` job for the market.

Existing assignments are never moved, so cluster ids stay stable for
ContextWard's lifecycle reconciliation.
"""

from __future__ import annotations

import logging
import math
import os
from collections import Counter
from typing import Optional

from .db import enqueue_if_idle, parse_vector, vector_literal
from .embed import get_embedder

logger = logging.getLogger(__name__)

LABEL_MAX = 120


def _threshold() -> float:
    return float(os.environ.get("CLUSTER_THRESHOLD", "0.78"))


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def extraction_text(ext: dict) -> str:
    parts = [ext.get("complaint_text") or ""]
    if ext.get("workflow"):
        parts.append(f"({ext['workflow']})")
    return " ".join(parts)


def _embed_missing(conn, market_key: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.extraction_json FROM review_extractions e
            JOIN normalized_reviews n ON n.id = e.review_id
            LEFT JOIN extraction_embeddings ee ON ee.extraction_id = e.id
            WHERE n.metadata_json->>'market_key' = %s
              AND e.validation_errors IS NULL AND ee.extraction_id IS NULL
            ORDER BY e.id
            """,
            (market_key,),
        )
        rows = cur.fetchall()
    if not rows:
        return 0
    embedder = get_embedder()
    for start in range(0, len(rows), 64):
        batch = rows[start:start + 64]
        vectors = embedder.embed([extraction_text(r[1]) for r in batch])
        with conn.cursor() as cur:
            for (ext_id, _), vec in zip(batch, vectors):
                cur.execute(
                    "INSERT INTO extraction_embeddings (extraction_id, model_key, embedding) VALUES (%s, %s, %s::vector) ON CONFLICT DO NOTHING",
                    (ext_id, embedder.model_key, vector_literal(vec)),
                )
    return len(rows)


class _Cluster:
    def __init__(self, cluster_id: int, category: str):
        self.id = cluster_id
        self.category = category
        self._sum: Optional[list[float]] = None
        self.size = 0

    def add(self, vec: list[float]) -> None:
        self._sum = list(vec) if self._sum is None else [a + b for a, b in zip(self._sum, vec)]
        self.size += 1

    @property
    def centroid(self) -> list[float]:
        return _unit(self._sum or [])


def assign(clusters: list[_Cluster], category: str, vec: list[float], threshold: float) -> tuple[Optional[_Cluster], float]:
    """Closest same-category cluster at or above threshold, else (None, best_sim)."""
    best, best_sim = None, -1.0
    for c in clusters:
        if c.category != category:
            continue
        sim = _dot(c.centroid, vec)
        if sim > best_sim:
            best, best_sim = c, sim
    return (best, best_sim) if best is not None and best_sim >= threshold else (None, best_sim)


def handle_cluster(conn, job: dict) -> dict:
    market_key = job["market_key"]
    threshold = _threshold()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"cluster:{market_key}",))

    embedded = _embed_missing(conn, market_key)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.category, ee.embedding::text
            FROM gap_clusters c
            JOIN gap_cluster_members m ON m.gap_cluster_id = c.id
            JOIN extraction_embeddings ee ON ee.extraction_id = m.extraction_id
            WHERE c.market_key = %s AND c.status = 'active'
            ORDER BY c.id
            """,
            (market_key,),
        )
        clusters: dict[int, _Cluster] = {}
        for cid, category, emb in cur.fetchall():
            clusters.setdefault(cid, _Cluster(cid, category)).add(_unit(parse_vector(emb)))

        cur.execute(
            """
            SELECT e.id, e.extraction_json->>'category', ee.embedding::text
            FROM review_extractions e
            JOIN normalized_reviews n ON n.id = e.review_id
            JOIN extraction_embeddings ee ON ee.extraction_id = e.id
            LEFT JOIN gap_cluster_members m ON m.extraction_id = e.id
            WHERE n.metadata_json->>'market_key' = %s AND m.extraction_id IS NULL
            ORDER BY e.id
            """,
            (market_key,),
        )
        pending = cur.fetchall()

    pool = list(clusters.values())
    touched: set[int] = set()
    created = 0
    with conn.cursor() as cur:
        for ext_id, category, emb in pending:
            vec = _unit(parse_vector(emb))
            target, sim = assign(pool, category, vec, threshold)
            if target is None:
                cur.execute(
                    "INSERT INTO gap_clusters (market_key, category, status) VALUES (%s, %s, 'active') RETURNING id",
                    (market_key, category),
                )
                target = _Cluster(cur.fetchone()[0], category)
                pool.append(target)
                created += 1
                sim = 1.0
            cur.execute(
                "INSERT INTO gap_cluster_members (gap_cluster_id, extraction_id, similarity_score) VALUES (%s, %s, %s)",
                (target.id, ext_id, round(max(0.0, min(1.0, sim)), 5)),
            )
            target.add(vec)
            touched.add(target.id)

        for cid in sorted(touched):
            _relabel(cur, cid)

    if touched:
        enqueue_if_idle(conn, "score", market_key)
    conn.commit()
    result = {"market_key": market_key, "embedded": embedded, "assigned": len(pending),
              "clusters_created": created, "clusters_touched": len(touched)}
    logger.info("Cluster %s", result)
    return result


def _relabel(cur, cluster_id: int) -> None:
    cur.execute(
        """
        SELECT e.extraction_json, e.review_id, ee.embedding::text, n.competitor_name
        FROM gap_cluster_members m
        JOIN review_extractions e ON e.id = m.extraction_id
        JOIN extraction_embeddings ee ON ee.extraction_id = e.id
        JOIN normalized_reviews n ON n.id = e.review_id
        WHERE m.gap_cluster_id = %s
        """,
        (cluster_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return
    vectors = [_unit(parse_vector(r[2])) for r in rows]
    centroid = _unit([sum(col) for col in zip(*vectors)])
    medoid = max(range(len(rows)), key=lambda i: _dot(vectors[i], centroid))
    exts = [r[0] for r in rows]

    label = (exts[medoid].get("complaint_text") or "").strip()
    if len(label) > LABEL_MAX:
        label = label[: LABEL_MAX - 1].rstrip() + "…"

    reviews = len({r[1] for r in rows})
    segments = Counter(e["segment"].strip().lower() for e in exts if e.get("segment"))
    incumbents = Counter(
        (e.get("incumbent_named") or r[3] or "").strip() for e, r in zip(exts, rows)
    )
    incumbents.pop("", None)
    parts = [f"{len(rows)} complaint{'s' if len(rows) != 1 else ''} from {reviews} source document{'s' if reviews != 1 else ''}."]
    if segments:
        parts.append("Segment: " + ", ".join(s for s, _ in segments.most_common(2)) + ".")
    if incumbents:
        parts.append("Incumbents: " + ", ".join(s for s, _ in incumbents.most_common(3)) + ".")
    quote = (exts[medoid].get("source_quote") or "").strip()
    if quote:
        parts.append(f'Example: "{quote[:160]}"')
    cur.execute(
        "UPDATE gap_clusters SET cluster_label = %s, summary = %s WHERE id = %s",
        (label or None, " ".join(parts), cluster_id),
    )
