"""Ingestion stage: pull public demand signals into raw_documents.

Each `ingest` queue job names an adapter and a market:

    {"adapter": "hn", "market_key": "bookkeeping-qc", "query": "quickbooks export"}
    {"adapter": "app_store", "market_key": "...", "app_id": "1234", "country": "ca",
     "competitor": "QuickBooks"}
    {"adapter": "file", "market_key": "...", "path": "/data/g2-export.jsonl",
     "source_key": "g2", "competitor": "Xero"}

Adapters need no credentials:
- hn         Hacker News stories and comments via the public Algolia API.
- app_store  Apple's public customer-review RSS feed (latest 500 per app/country).
- file       JSONL or CSV you exported yourself (G2, Capterra, surveys, support tickets).

Raw rows are immutable: re-ingesting the same (source, external_id) is a no-op.
Author names are never stored; only a salted hash is kept.
Each new row queues one `normalize` job.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional

import requests

from .db import enqueue_job, stable_key
from .dedup import content_hash

logger = logging.getLogger(__name__)

PARSER_VERSION = "ingest-1.0.0"
HTTP_TIMEOUT = 30
USER_AGENT = "gap-engine/1.0 (market research; contact via repository owner)"

SOURCE_NAMES = {
    "hn": "Hacker News",
    "app_store": "Apple App Store",
    "file": "File import",
}


def author_hash(author: Optional[str]) -> Optional[str]:
    """Salted SHA-256 of an author handle. Set GAP_AUTHOR_SALT in production."""
    if not author:
        return None
    salt = os.environ.get("GAP_AUTHOR_SALT", "")
    return hashlib.sha256((salt + author.strip().lower()).encode()).hexdigest()[:32]


# ─── Adapters ──────────────────────────────────────────────────────────────
# Each adapter yields dicts with: external_id, item_type, text, and optionally
# title, url, authored_at (datetime), parent_external_id, rating_5, language,
# author_hash, competitor, product, payload (source item without author names).


def _hn_items(job: dict, cursor: dict) -> Iterator[dict]:
    query = job["query"]
    since = int(cursor.get("max_created_at_i", job.get("since_epoch", 0)))
    max_pages = int(job.get("max_pages", 5))
    for page in range(max_pages):
        r = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": query,
                "tags": "(story,comment)",
                "numericFilters": f"created_at_i>{since}",
                "hitsPerPage": 100,
                "page": page,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if not hits:
            return
        for h in hits:
            is_comment = "comment" in (h.get("_tags") or [])
            text = h.get("comment_text") if is_comment else (h.get("story_text") or "")
            title = None if is_comment else h.get("title")
            if not (text or title):
                continue
            yield {
                "external_id": str(h["objectID"]),
                "item_type": "comment" if is_comment else "post",
                "title": title,
                "text": text or "",
                "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
                "authored_at": datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
                "parent_external_id": str(h["parent_id"]) if h.get("parent_id") else None,
                "author_hash": author_hash(h.get("author")),
                "cursor_value": int(h["created_at_i"]),
                "payload": {k: v for k, v in h.items() if k not in ("author", "_highlightResult")},
            }
        time.sleep(0.5)


def _app_store_items(job: dict, cursor: dict) -> Iterator[dict]:
    app_id = str(job["app_id"])
    country = job.get("country", "ca")
    for page in range(1, int(job.get("max_pages", 10)) + 1):
        r = requests.get(
            f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json",
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code in (400, 404):
            return
        r.raise_for_status()
        entries = r.json().get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        reviews = [e for e in entries if "im:rating" in e]
        if not reviews:
            return
        for e in reviews:
            author = (e.get("author") or {}).get("name", {}).get("label")
            payload = {k: v for k, v in e.items() if k != "author"}
            yield {
                "external_id": f"{country}:{e['id']['label']}",
                "item_type": "review",
                "title": (e.get("title") or {}).get("label"),
                "text": (e.get("content") or {}).get("label", ""),
                "url": None,
                "authored_at": _parse_dt((e.get("updated") or {}).get("label")),
                "rating_5": float(e["im:rating"]["label"]),
                "author_hash": author_hash(author),
                "country": country.upper(),
                "payload": payload,
            }
        time.sleep(0.5)


def _file_items(job: dict, cursor: dict) -> Iterator[dict]:
    """JSONL or CSV with columns: external_id, text, and optional title, url,
    authored_at, rating (0-5), author, language, item_type, competitor, product."""
    path = job["path"]
    with open(path, newline="", encoding="utf-8") as fh:
        rows = (json.loads(line) for line in fh if line.strip()) if path.endswith((".jsonl", ".ndjson")) else csv.DictReader(fh)
        for i, row in enumerate(rows):
            text = row.get("text") or row.get("body") or ""
            if not text.strip():
                continue
            rating = row.get("rating") or row.get("rating_5")
            yield {
                "external_id": str(row.get("external_id") or row.get("id") or f"{os.path.basename(path)}:{i}"),
                "item_type": row.get("item_type") if row.get("item_type") in ("review", "post", "comment", "forum_thread") else "review",
                "title": row.get("title"),
                "text": text,
                "url": row.get("url"),
                "authored_at": _parse_dt(row.get("authored_at")),
                "rating_5": float(rating) if rating not in (None, "") else None,
                "language": row.get("language"),
                "author_hash": author_hash(row.get("author")),
                "competitor": row.get("competitor"),
                "product": row.get("product"),
                "payload": {k: v for k, v in row.items() if k != "author"},
            }


ADAPTERS: dict[str, Callable[[dict, dict], Iterator[dict]]] = {
    "hn": _hn_items,
    "app_store": _app_store_items,
    "file": _file_items,
}


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ─── Handler ───────────────────────────────────────────────────────────────


def _source_id(conn, source_key: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sources (source_key, display_name) VALUES (%s, %s)
            ON CONFLICT (source_key) DO UPDATE SET source_key = EXCLUDED.source_key
            RETURNING id
            """,
            (source_key, SOURCE_NAMES.get(source_key, source_key)),
        )
        return cur.fetchone()[0]


def scope_key_for(job: dict) -> str:
    adapter = job["adapter"]
    detail = {"hn": job.get("query"), "app_store": f"{job.get('country', 'ca')}:{job.get('app_id')}", "file": job.get("path")}[adapter]
    return f"{job['market_key']}|{adapter}|{detail}"


def handle_ingest(conn, job: dict) -> dict:
    adapter = job.get("adapter")
    if adapter not in ADAPTERS:
        raise ValueError(f"Unknown adapter {adapter!r}; expected one of {sorted(ADAPTERS)}")
    if not job.get("market_key"):
        raise ValueError("Ingest job needs market_key")

    source_key = job.get("source_key") or adapter
    source_id = _source_id(conn, source_key)
    scope_key = scope_key_for(job)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cursor_json FROM ingestion_runs
            WHERE source_id = %s AND scope_key = %s AND status = 'succeeded'
            ORDER BY finished_at DESC NULLS LAST LIMIT 1
            """,
            (source_id, scope_key),
        )
        row = cur.fetchone()
        cursor = (row[0] or {}) if row else {}
        cur.execute(
            """
            INSERT INTO ingestion_runs (source_id, scope_key, run_type, status, started_at)
            VALUES (%s, %s, %s, 'running', now()) RETURNING id
            """,
            (source_id, scope_key, "backfill" if not cursor else "recent"),
        )
        run_id = cur.fetchone()[0]
    conn.commit()

    ingest_meta = {
        "run_id": run_id,
        "market_key": job["market_key"],
        "competitor": job.get("competitor"),
        "product": job.get("product"),
    }
    fetched = inserted = 0
    max_cursor = cursor.get("max_created_at_i", 0)
    try:
        for item in ADAPTERS[adapter](job, cursor):
            fetched += 1
            max_cursor = max(max_cursor, item.get("cursor_value", 0))
            payload = dict(item["payload"])
            payload["_ingest"] = {
                **ingest_meta,
                "competitor": item.get("competitor") or ingest_meta["competitor"],
                "product": item.get("product") or ingest_meta["product"],
                "rating_5": item.get("rating_5"),
                "language": item.get("language"),
                "country": item.get("country") or job.get("country"),
                "author_hash": item.get("author_hash"),
            }
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw_documents
                      (source_id, external_id, parent_external_id, canonical_url, authored_at,
                       item_type, title, raw_text, raw_payload, parser_version, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id, external_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        source_id, item["external_id"], item.get("parent_external_id"), item.get("url"),
                        item.get("authored_at"), item["item_type"], item.get("title"), item["text"],
                        payload, PARSER_VERSION, content_hash(item.get("title") or "", item["text"]),
                    ),
                )
                new = cur.fetchone()
            if new:
                inserted += 1
                enqueue_job(conn, "normalize", {"raw_document_id": new[0]},
                            idempotency_key=stable_key("normalize", new[0]))
            conn.commit()
    except Exception as e:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingestion_runs SET status='failed', finished_at=now(), stats_json=%s WHERE id=%s",
                ({"fetched": fetched, "inserted": inserted, "error": str(e)[:500]}, run_id),
            )
        conn.commit()
        raise

    stats = {"fetched": fetched, "inserted": inserted}
    new_cursor = {"max_created_at_i": max_cursor} if max_cursor else cursor
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ingestion_runs SET status='succeeded', finished_at=now(), stats_json=%s, cursor_json=%s WHERE id=%s",
            (stats, new_cursor, run_id),
        )
    conn.commit()
    logger.info("Ingest %s: %s", scope_key, stats)
    return {"run_id": run_id, **stats}
