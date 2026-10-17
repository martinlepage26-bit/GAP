"""Scoring stage: one gap_scores row per active cluster in a market.

GapScore = 0.20F + 0.20I + 0.15B + 0.15W + 0.10P + 0.05S + 0.10Fe + 0.05D - penalty,
clamped to [0, 1]. Every component is in [0, 1] and stored alongside the total.

Measured from the evidence:
  F  frequency    distinct source documents, weighted by sources.trust_weight,
                  log-scaled so 50 weighted documents = 1.0
  I  intensity    mean of 0.75 x normalized severity + 0.25 x (money, compliance
                  or risk signal present)
  B  breadth      distinct incumbents named or reviewed / 5
  W  willingness  mean willingness_to_pay / 3
  P  workaround   share of complaints describing a workaround
  S  segment      share of complaints naming the most common segment

Owner judgement (gap_cluster_assessments; neutral defaults when absent):
  Fe feasibility  0.5     D defensibility  0.5     incumbent-fix penalty  0

Automatic penalty: 0.10 when all evidence comes from a single source.
"""

from __future__ import annotations

import logging
import math
from collections import Counter

from .models import GapScoreInput, GapScoreOutput

logger = logging.getLogger(__name__)

ALGORITHM_VERSION = "gs-1.0.0"
F_SATURATION = 50.0
MONOCULTURE_PENALTY = 0.10
HIGH_COST_SIGNALS = {"money_loss", "compliance", "risk"}

WEIGHTS = {
    "frequency_score": 0.20,
    "intensity_score": 0.20,
    "breadth_score": 0.15,
    "wtp_score": 0.15,
    "workaround_score": 0.10,
    "segment_score": 0.05,
    "feasibility_score": 0.10,
    "defensibility_score": 0.05,
}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_components(evidence: list[dict], assessment: dict | None = None) -> GapScoreInput:
    """evidence: one dict per complaint with keys extraction (dict), review_id,
    source_id, trust_weight, competitor."""
    assessment = assessment or {}
    n = len(evidence)
    if n == 0:
        raise ValueError("Cannot score an empty cluster")
    exts = [e["extraction"] for e in evidence]

    doc_weight: dict[int, float] = {}
    for e in evidence:
        doc_weight[e["review_id"]] = float(e.get("trust_weight") or 1.0)
    frequency = math.log1p(sum(doc_weight.values())) / math.log1p(F_SATURATION)

    def signal_types(x: dict) -> set[str]:
        return {s.get("type") for s in x.get("pain_signals") or []}

    intensity = sum(
        0.75 * (int(x.get("severity", 1)) - 1) / 4 + 0.25 * bool(signal_types(x) & HIGH_COST_SIGNALS)
        for x in exts
    ) / n

    incumbents = {
        (x.get("incumbent_named") or e.get("competitor") or "").strip().lower()
        for x, e in zip(exts, evidence)
    } - {""}
    breadth = len(incumbents) / 5

    wtp = sum(int(x.get("willingness_to_pay", 0)) for x in exts) / (3 * n)
    workaround = sum(bool(x.get("has_workaround")) or "workaround" in signal_types(x) for x in exts) / n

    segments = Counter((x.get("segment") or "").strip().lower() for x in exts)
    segments.pop("", None)
    segment = (segments.most_common(1)[0][1] / n) if segments else 0.0

    penalty = float(assessment.get("incumbent_fix_penalty") or 0.0)
    if len({e["source_id"] for e in evidence}) == 1:
        penalty += MONOCULTURE_PENALTY

    return GapScoreInput(
        frequency_score=_clamp(frequency),
        intensity_score=_clamp(intensity),
        breadth_score=_clamp(breadth),
        wtp_score=_clamp(wtp),
        workaround_score=_clamp(workaround),
        segment_score=_clamp(segment),
        feasibility_score=_clamp(float(assessment.get("feasibility_score", 0.5))),
        defensibility_score=_clamp(float(assessment.get("defensibility_score", 0.5))),
        penalty_score=_clamp(penalty),
    )


def total(components: GapScoreInput) -> GapScoreOutput:
    raw = sum(getattr(components, k) * w for k, w in WEIGHTS.items()) - components.penalty_score
    return GapScoreOutput(total_score=_clamp(raw), components=components, algorithm_version=ALGORITHM_VERSION)


def handle_score(conn, job: dict) -> dict:
    market_key = job["market_key"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.gap_cluster_id, e.extraction_json, e.review_id, n.source_id,
                   s.trust_weight, n.competitor_name
            FROM gap_clusters c
            JOIN gap_cluster_members m ON m.gap_cluster_id = c.id
            JOIN review_extractions e ON e.id = m.extraction_id
            JOIN normalized_reviews n ON n.id = e.review_id
            JOIN sources s ON s.id = n.source_id
            WHERE c.market_key = %s AND c.status = 'active'
            """,
            (market_key,),
        )
        by_cluster: dict[int, list[dict]] = {}
        for cid, ext, review_id, source_id, trust, competitor in cur.fetchall():
            by_cluster.setdefault(cid, []).append({
                "extraction": ext, "review_id": review_id, "source_id": source_id,
                "trust_weight": trust, "competitor": competitor,
            })

        cur.execute(
            """
            SELECT a.gap_cluster_id, a.feasibility_score, a.defensibility_score, a.incumbent_fix_penalty
            FROM gap_cluster_assessments a JOIN gap_clusters c ON c.id = a.gap_cluster_id
            WHERE c.market_key = %s
            """,
            (market_key,),
        )
        assessments = {
            r[0]: {"feasibility_score": r[1], "defensibility_score": r[2], "incumbent_fix_penalty": r[3]}
            for r in cur.fetchall()
        }

        for cid, evidence in by_cluster.items():
            out = total(compute_components(evidence, assessments.get(cid)))
            c = out.components
            cur.execute(
                """
                INSERT INTO gap_scores
                  (gap_cluster_id, algorithm_version, frequency_score, intensity_score, breadth_score,
                   wtp_score, workaround_score, segment_score, feasibility_score, defensibility_score,
                   penalty_score, total_score, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (gap_cluster_id) DO UPDATE SET
                  algorithm_version = EXCLUDED.algorithm_version,
                  frequency_score = EXCLUDED.frequency_score,
                  intensity_score = EXCLUDED.intensity_score,
                  breadth_score = EXCLUDED.breadth_score,
                  wtp_score = EXCLUDED.wtp_score,
                  workaround_score = EXCLUDED.workaround_score,
                  segment_score = EXCLUDED.segment_score,
                  feasibility_score = EXCLUDED.feasibility_score,
                  defensibility_score = EXCLUDED.defensibility_score,
                  penalty_score = EXCLUDED.penalty_score,
                  total_score = EXCLUDED.total_score,
                  computed_at = now()
                """,
                (
                    cid, out.algorithm_version, round(c.frequency_score, 5), round(c.intensity_score, 5),
                    round(c.breadth_score, 5), round(c.wtp_score, 5), round(c.workaround_score, 5),
                    round(c.segment_score, 5), round(c.feasibility_score, 5), round(c.defensibility_score, 5),
                    round(c.penalty_score, 5), round(out.total_score, 5),
                ),
            )
    conn.commit()
    logger.info("Scored %d clusters in %s", len(by_cluster), market_key)
    return {"market_key": market_key, "clusters_scored": len(by_cluster)}
