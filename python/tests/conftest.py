"""Test database fixture.

Integration tests need an empty Postgres with pgvector and pg_trgm:

    GAP_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/gap_test pytest
        (a throwaway database; tests apply every migration into it)

or, with no socket available, an in-memory PGlite subprocess:

    PGLITE_NODE_DIR=/path/with/node_modules pytest
        (that directory needs: npm install @electric-sql/pglite @electric-sql/pglite-pgvector)

Without either, integration tests are skipped and unit tests still run.
"""

from __future__ import annotations

import glob
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIGRATIONS = sorted(glob.glob(os.path.join(ROOT, "..", "supabase", "migrations", "*.sql")))

ROLES = """
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
END $$;
"""


def _connect():
    url = os.environ.get("GAP_TEST_DATABASE_URL")
    if url:
        import psycopg2

        conn = psycopg2.connect(url)

        def run(sql):
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()

        return conn, run
    if os.environ.get("PGLITE_NODE_DIR"):
        from pgbridge import BridgeConnection

        conn = BridgeConnection()
        return conn, conn.exec_script
    return None, None


@pytest.fixture
def db(monkeypatch):
    conn, run = _connect()
    if conn is None:
        pytest.skip("set GAP_TEST_DATABASE_URL or PGLITE_NODE_DIR for integration tests")
    run(ROLES)
    for path in MIGRATIONS:
        with open(path, encoding="utf-8") as fh:
            run(fh.read())
    monkeypatch.setenv("EMBED_PROVIDER", "hash")
    import gap_engine.embed as embed

    monkeypatch.setattr(embed, "_embedder", None)
    yield conn
    conn.close()
