"""Pipeline tests. Unit tests always run; integration tests need the `db` fixture."""

from __future__ import annotations

import json
import os

import pytest

from gap_engine import worker
from gap_engine.cluster import _Cluster, _unit, assign
from gap_engine.db import enqueue_job
from gap_engine.embed import HashEmbedder
from gap_engine.extract import ExtractionError
from gap_engine.ingest import _file_items, author_hash
from gap_engine.models import Extraction
from gap_engine.normalize import clean_text, detect_language
from gap_engine.score import ALGORITHM_VERSION, compute_components, total

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "reviews.jsonl")
MARKET = "bookkeeping-qc"


# ─── Unit ──────────────────────────────────────────────────────────────────


def test_clean_text_strips_html_and_entities():
    assert clean_text("<p>Export &amp; import<br>   fail <i>daily</i>") == "Export & import\nfail daily"


def test_detect_language():
    assert detect_language("Le prix de l'abonnement est trop élevé pour nous") == "fr"
    assert detect_language("The export to Excel fails for the monthly close") == "en"
    assert detect_language("ok") is None


def test_hash_embedder_is_normalized_and_deterministic():
    e = HashEmbedder()
    a, b = e.embed(["Export to Excel fails", "Export to Excel fails"])
    assert a == b and len(a) == 384
    assert abs(sum(x * x for x in a) - 1.0) < 1e-9


def test_assign_respects_category_and_threshold():
    c = _Cluster(1, "feature_gap")
    c.add(_unit([1.0, 0.0]))
    assert assign([c], "feature_gap", _unit([0.9, 0.1]), 0.78)[0] is c
    assert assign([c], "price_gap", _unit([1.0, 0.0]), 0.78)[0] is None
    assert assign([c], "feature_gap", _unit([0.0, 1.0]), 0.78)[0] is None


def test_file_adapter_never_keeps_author_names():
    items = list(_file_items({"path": FIXTURE}, {}))
    assert len(items) == 9
    blob = json.dumps([i["payload"] for i in items], default=str)
    assert "Alice Tremblay" not in blob
    assert items[0]["author_hash"] == author_hash("Alice Tremblay")


def _ext(**kw) -> dict:
    base = dict(complaint_text="Report export to Excel fails", category="feature_gap", severity=4,
                has_workaround=True, willingness_to_pay=2, source_quote="export fails",
                segment="bookkeepers", pain_signals=[{"type": "time_loss", "evidence": "lose an afternoon"}])
    base.update(kw)
    return Extraction(**base).model_dump()


def test_score_components_and_bounds():
    evidence = [
        {"extraction": _ext(), "review_id": 1, "source_id": 1, "trust_weight": 1, "competitor": "QuickBooks"},
        {"extraction": _ext(willingness_to_pay=3, pain_signals=[{"type": "money_loss", "evidence": "costs us"}]),
         "review_id": 2, "source_id": 2, "trust_weight": 1, "competitor": "Xero"},
    ]
    c = compute_components(evidence)
    assert c.breadth_score == pytest.approx(2 / 5)
    assert c.wtp_score == pytest.approx(5 / 6)
    assert c.workaround_score == 1.0
    assert c.segment_score == 1.0
    assert c.intensity_score == pytest.approx((0.75 * 0.75 + 0.75 * 0.75 + 0.25) / 2)
    assert c.penalty_score == 0.0
    out = total(c)
    assert 0 <= out.total_score <= 1 and out.algorithm_version == ALGORITHM_VERSION


def test_single_source_penalty_and_owner_assessment():
    evidence = [{"extraction": _ext(), "review_id": 1, "source_id": 7, "trust_weight": 1, "competitor": None}]
    base = compute_components(evidence)
    assert base.penalty_score == pytest.approx(0.10)
    judged = compute_components(evidence, {"feasibility_score": 1, "defensibility_score": 0, "incumbent_fix_penalty": 0.2})
    assert judged.penalty_score == pytest.approx(0.30)
    assert total(judged).total_score < total(base).total_score


# ─── Integration ───────────────────────────────────────────────────────────


def fake_extract(review_text, **kwargs):
    """Deterministic stand-in for the LLM, keyed on review wording."""
    t = review_text.lower()
    items = []
    if "excel" in t:
        items.append(Extraction(
            complaint_text="Monthly report export to Excel fails",
            category="feature_gap", segment="bookkeeping firms", workflow="monthly close",
            severity=4, has_workaround="spreadsheet" in t or "by hand" in t,
            willingness_to_pay=3 if "pay" in t else 1,
            incumbent_named=None,
            pain_signals=[{"type": "time_loss", "evidence": "lose an afternoon"}],
            source_quote=review_text[:40],
        ))
    if "prix" in t or "price" in t:
        items.append(Extraction(
            complaint_text="Subscription price too high for small accounting firms",
            category="price_gap", segment="small accounting firms", severity=3,
            has_workaround=False, willingness_to_pay=0, pain_signals=[{"type": "money_loss", "evidence": "trop élevé"}],
            source_quote=review_text[:40],
        ))
    if "french" in t:
        items.append(Extraction(
            complaint_text="No French-language interface for Québec staff",
            category="geography_language_gap", segment="Québec firms", severity=3,
            has_workaround=False, willingness_to_pay=1, source_quote=review_text[:40],
        ))
    return items


def _count(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _ingest_fixture(conn):
    enqueue_job(conn, "ingest", {"adapter": "file", "market_key": MARKET, "path": FIXTURE, "source_key": "g2"})
    conn.commit()


def test_full_pipeline_feeds_contextward(db, monkeypatch):
    monkeypatch.setenv("CLUSTER_THRESHOLD", "0.5")
    monkeypatch.setattr(worker, "extract_feedback", fake_extract)
    _ingest_fixture(db)

    totals = worker.drain(db, "http://unused", "unused", "gpt-4.1-mini")
    assert totals["ingest"] == 1

    assert _count(db, "SELECT count(*) FROM raw_documents") == 9
    # r7 is too short to keep.
    assert _count(db, "SELECT count(*) FROM normalized_reviews") == 8
    # r5 is an exact copy of r1; r6 differs only in punctuation and joins the same group.
    assert _count(db, "SELECT count(*) FROM duplicate_groups") == 1
    assert _count(db, "SELECT count(*) FROM duplicate_group_members WHERE reason_json->>'reason' = 'exact_text'") == 1
    assert _count(db, "SELECT count(*) FROM duplicate_group_members WHERE reason_json->>'reason' = 'semantic'") == 1
    # 8 kept reviews minus 1 exact and 1 semantic duplicate reach the LLM.
    assert _count(db, "SELECT count(*) FROM extraction_jobs WHERE status = 'done'") == 6
    assert _count(db, "SELECT count(*) FROM review_extractions") == 5
    assert _count(db, "SELECT count(*) FROM queue_jobs WHERE status <> 'done'") == 0

    with db.cursor() as cur:
        cur.execute("""
            SELECT c.category, c.cluster_label, c.summary, count(m.extraction_id)
            FROM gap_clusters c JOIN gap_cluster_members m ON m.gap_cluster_id = c.id
            WHERE c.market_key = %s GROUP BY c.id ORDER BY c.category
        """, (MARKET,))
        clusters = cur.fetchall()
    assert [(c[0], c[3]) for c in clusters] == [
        ("feature_gap", 3), ("geography_language_gap", 1), ("price_gap", 1)]
    assert clusters[0][1] == "Monthly report export to Excel fails"
    assert "3 complaints from 3 source documents" in clusters[0][2]

    # The exact columns ContextWard's GapClient selects (Integrations.swift), all within 0..1.
    with db.cursor() as cur:
        cur.execute("""
            SELECT c.id, c.market_key, c.category, c.cluster_label, c.summary, c.created_at,
                   s.total_score, s.frequency_score, s.intensity_score, s.breadth_score,
                   s.wtp_score, s.workaround_score, s.segment_score
            FROM gap_clusters c JOIN gap_scores s ON s.gap_cluster_id = c.id
            WHERE c.status = 'active' ORDER BY s.total_score DESC
        """)
        rows = cur.fetchall()
    assert len(rows) == 3
    for row in rows:
        assert all(0 <= float(v) <= 1 for v in row[6:])
    assert rows[0][2] == "feature_gap"  # most evidence, workarounds, willingness to pay


def test_rerun_is_idempotent_and_keeps_cluster_ids(db, monkeypatch):
    monkeypatch.setenv("CLUSTER_THRESHOLD", "0.5")
    monkeypatch.setattr(worker, "extract_feedback", fake_extract)
    _ingest_fixture(db)
    worker.drain(db, "http://unused", "unused", "gpt-4.1-mini")
    with db.cursor() as cur:
        cur.execute("SELECT id FROM gap_clusters ORDER BY id")
        before = cur.fetchall()

    _ingest_fixture(db)
    worker.drain(db, "http://unused", "unused", "gpt-4.1-mini")
    with db.cursor() as cur:
        cur.execute("SELECT result_json FROM queue_jobs WHERE queue_name = 'ingest' ORDER BY id DESC LIMIT 1")
        assert cur.fetchone()[0]["inserted"] == 0
        cur.execute("SELECT id FROM gap_clusters ORDER BY id")
        assert cur.fetchall() == before
    assert _count(db, "SELECT count(*) FROM review_extractions") == 5


def test_llm_failure_retries_instead_of_recording_no_complaints(db, monkeypatch):
    def failing(review_text, **kwargs):
        raise ExtractionError("provider rate limit (429)")

    monkeypatch.setattr(worker, "extract_feedback", failing)
    _ingest_fixture(db)
    worker.drain(db, "http://unused", "unused", "gpt-4.1-mini")
    assert _count(db, "SELECT count(*) FROM review_extractions") == 0
    assert _count(db, "SELECT count(*) FROM extraction_jobs WHERE status = 'retry'") == 6
    assert _count(db, "SELECT count(*) FROM extraction_jobs WHERE status = 'done'") == 0
