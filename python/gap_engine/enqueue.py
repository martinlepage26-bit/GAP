"""Queue pipeline work from the command line.

    python -m gap_engine.enqueue ingest --market bookkeeping-qc --adapter hn --query "quickbooks export"
    python -m gap_engine.enqueue ingest --market bookkeeping-qc --adapter app_store --app-id 584606479 \
        --country ca --competitor "QuickBooks"
    python -m gap_engine.enqueue ingest --market bookkeeping-qc --adapter file --path exports/g2.jsonl \
        --source-key g2 --competitor Xero
    python -m gap_engine.enqueue cluster --market bookkeeping-qc
    python -m gap_engine.enqueue score --market bookkeeping-qc

Schedule the ingest commands (cron, launchd, a Supabase cron calling a
small runner) to keep a market fresh; the HN adapter resumes from its cursor.
"""

from __future__ import annotations

import argparse

from .db import enqueue_if_idle, enqueue_job, get_db_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue Gap Discovery Engine jobs")
    sub = parser.add_subparsers(dest="stage", required=True)

    ing = sub.add_parser("ingest")
    ing.add_argument("--market", required=True)
    ing.add_argument("--adapter", required=True, choices=["hn", "app_store", "file"])
    ing.add_argument("--query")
    ing.add_argument("--app-id")
    ing.add_argument("--country", default="ca")
    ing.add_argument("--path")
    ing.add_argument("--source-key")
    ing.add_argument("--competitor")
    ing.add_argument("--product")
    ing.add_argument("--max-pages", type=int)

    for stage in ("cluster", "score"):
        sub.add_parser(stage).add_argument("--market", required=True)

    args = parser.parse_args()
    conn = get_db_connection()

    if args.stage == "ingest":
        needed = {"hn": "query", "app_store": "app_id", "file": "path"}[args.adapter]
        if not getattr(args, needed):
            parser.error(f"--{needed.replace('_', '-')} is required for adapter {args.adapter}")
        payload = {"adapter": args.adapter, "market_key": args.market}
        for key in ("query", "app_id", "country", "path", "source_key", "competitor", "product", "max_pages"):
            value = getattr(args, key)
            if value is not None and (key != "country" or args.adapter == "app_store"):
                payload[key] = value
        enqueue_job(conn, "ingest", payload)
        queued = True
    else:
        queued = enqueue_if_idle(conn, args.stage, args.market)
    conn.commit()
    print("queued" if queued else "already queued")


if __name__ == "__main__":
    main()
