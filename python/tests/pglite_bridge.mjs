// Test-only bridge: in-memory PGlite (Postgres 17 + pgvector + pg_trgm) driven
// over stdin/stdout with one JSON request per line. Used by tests/pgbridge.py
// when no real Postgres is available (for example inside a sandbox that cannot
// open sockets). Requires: npm install --no-save @electric-sql/pglite @electric-sql/pglite-pgvector
import { createInterface } from 'node:readline'
import { PGlite } from '@electric-sql/pglite'
import { pg_trgm } from '@electric-sql/pglite/contrib/pg_trgm'
import { vector } from '@electric-sql/pglite-pgvector'

const db = await PGlite.create({ extensions: { vector, pg_trgm } })

const decode = (v) => {
  if (v && typeof v === 'object' && !Array.isArray(v) && '$bytes' in v) return Buffer.from(v.$bytes, 'hex')
  if (Array.isArray(v)) return v.map(decode)
  return v
}
const encode = (_k, v) => {
  if (typeof v === 'bigint') return Number(v)
  if (v instanceof Uint8Array) return { $bytes: Buffer.from(v).toString('hex') }
  if (v && v.type === 'Buffer' && Array.isArray(v.data)) return { $bytes: Buffer.from(v.data).toString('hex') }
  return v
}

process.stdout.write(JSON.stringify({ ready: true }) + '\n')
const rl = createInterface({ input: process.stdin })
for await (const line of rl) {
  const req = JSON.parse(line)
  let res
  try {
    if (req.exec) {
      await db.exec(req.sql)
      res = { rows: [], fields: [], rowCount: 0 }
    } else {
      const r = await db.query(req.sql, (req.params || []).map(decode), { rowMode: 'array' })
      res = { rows: r.rows, fields: r.fields.map((f) => f.name), rowCount: r.affectedRows ?? r.rows.length }
    }
  } catch (e) {
    res = { error: String(e.message || e), code: e.code || null }
  }
  process.stdout.write(JSON.stringify(res, encode) + '\n')
}
