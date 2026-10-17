"""Minimal psycopg2-compatible connection over the PGlite stdin/stdout bridge.

Covers what the pipeline uses: cursor() as a context manager, execute with
%s parameters, fetchone/fetchall, rowcount, RealDictCursor, commit/rollback
with psycopg2's implicit BEGIN. Test use only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

HERE = os.path.dirname(os.path.abspath(__file__))


class BridgeError(Exception):
    pass


def _param(v):
    if isinstance(v, Json):
        v = v.adapted
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$bytes": bytes(v).hex()}
    if isinstance(v, dict):
        return json.dumps(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_param(x) for x in v]
    return v


def _convert(sql: str) -> str:
    n = 0

    def sub(_m):
        nonlocal n
        n += 1
        return f"${n}"

    sql = re.sub(r"(?<!%)%s", sub, sql)
    return sql.replace("%%", "%")


class _Cursor:
    def __init__(self, conn, dict_rows: bool):
        self.conn = conn
        self.dict_rows = dict_rows
        self._rows: list = []
        self._fields: list = []
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if not self.conn._in_tx:
            self.conn._send({"sql": "BEGIN"})
            self.conn._in_tx = True
        res = self.conn._send({"sql": _convert(sql) if params is not None else sql.replace("%%", "%"),
                               "params": [_param(p) for p in (params or ())]})
        self._fields = res["fields"]
        self._rows = [_decode_row(r) for r in res["rows"]]
        self.rowcount = res["rowCount"]

    def fetchone(self):
        if not self._rows:
            return None
        row = self._rows.pop(0)
        return dict(zip(self._fields, row)) if self.dict_rows else tuple(row)

    def fetchall(self):
        rows, self._rows = self._rows, []
        return [dict(zip(self._fields, r)) if self.dict_rows else tuple(r) for r in rows]


def _decode_row(row):
    return [bytes.fromhex(v["$bytes"]) if isinstance(v, dict) and "$bytes" in v and len(v) == 1 else v for v in row]


class BridgeConnection:
    def __init__(self):
        # ESM resolves packages next to the script, so run a copy inside the
        # directory whose node_modules holds PGlite.
        node_dir = os.environ.get("PGLITE_NODE_DIR", HERE)
        script = os.path.join(node_dir, "pglite_bridge.mjs")
        if os.path.abspath(script) != os.path.join(HERE, "pglite_bridge.mjs"):
            shutil.copyfile(os.path.join(HERE, "pglite_bridge.mjs"), script)
        self.proc = subprocess.Popen(
            ["node", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=node_dir,
        )
        ready = json.loads(self.proc.stdout.readline())
        assert ready.get("ready"), ready
        self._in_tx = False

    def _send(self, req: dict) -> dict:
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        res = json.loads(self.proc.stdout.readline())
        if "error" in res:
            raise BridgeError(res["error"])
        return res

    def exec_script(self, sql: str) -> None:
        self._send({"sql": sql, "exec": True})

    def cursor(self, cursor_factory=None):
        return _Cursor(self, cursor_factory is RealDictCursor)

    def commit(self):
        if self._in_tx:
            self._send({"sql": "COMMIT"})
            self._in_tx = False

    def rollback(self):
        if self._in_tx:
            self._send({"sql": "ROLLBACK"})
            self._in_tx = False

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
