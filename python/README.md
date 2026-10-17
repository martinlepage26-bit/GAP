# Gap Discovery Engine — Python Pipeline

Market gap analysis tool that ingests public demand signals, extracts atomic
unmet needs via LLM, clusters them across competitors, and ranks the most
durable gaps with evidence-backed scoring.

## Architecture

```
[Sources] → Ingestion → Raw Documents → Normalization → Extraction →
  Semantic Dedup → Clustering → Scoring → Gap Cards + Evidence Ledger
```

## Modules

| Module | Purpose |
|--------|---------|
| `models.py` | Pydantic schemas for structured LLM extraction + validation |
| `ingest.py` | `ingest` stage: Hacker News, App Store reviews, JSONL/CSV files → `raw_documents` |
| `normalize.py` | `normalize` stage: clean text, FR/EN detection, exact dedupe → `normalized_reviews` |
| `embed.py` | `embed` stage: local 384-d embeddings, semantic dedupe, queues extraction |
| `extract.py` | LLM extraction with cost/token tracking, rate limiting, retries |
| `cluster.py` | `cluster` stage: complaints → `gap_clusters` per market, labels + summaries |
| `score.py` | `score` stage: weighted GapScore → `gap_scores` |
| `worker.py` | Postgres SKIP LOCKED queue workers, `--queue all --drain`, Celery/BullMQ examples |
| `enqueue.py` | CLI to queue ingest, cluster, and score jobs |
| `db.py` | Connection, jsonb adaptation, queue helpers, stale-job recovery |
| `dedup.py` | 4-stage dedup pipeline: exact → text → thread → pgvector semantic |
| `monitor.py` | Cost/token/rate-limit monitoring + drift detection + alerts |

## Pipeline

```
ingest ─► normalize ─► embed ─► extract ─► cluster ─► score
```

Each stage queues the next. A market (`market_key`, e.g. `bookkeeping-qc`) is
the unit of clustering and scoring; every ingest job names one.

- **ingest** needs no credentials. `hn` uses the public Algolia API and resumes
  from its cursor; `app_store` reads Apple's public review RSS (latest 500 per
  app and country); `file` imports your own JSONL/CSV exports (G2, Capterra,
  surveys, support tickets). Author names are never stored, only a salted
  hash (`GAP_AUTHOR_SALT`).
- **normalize** drops items under 20 characters and groups exact text
  duplicates within a market before any embedding or LLM spend.
- **embed** runs locally (fastembed, multilingual MiniLM, 384-d), so review
  text is not sent to a third party at this step. Near-duplicates
  (cosine ≥ 0.93, same market, language, and competitor) join a duplicate group
  and skip the LLM.
- **extract** stores one `review_extractions` row per atomic complaint. Model
  failures retry with backoff instead of being recorded as "no complaints".
- **cluster** assigns new complaints to the closest same-category cluster
  (cosine ≥ `CLUSTER_THRESHOLD`, default 0.78) or starts a new one. Existing
  assignments never move, so cluster ids stay stable for ContextWard.
- **score** writes all eight components and the total, each in 0–1.
  Feasibility and defensibility default to 0.5 until you set them in
  `gap_cluster_assessments`.

### Run it

```bash
python -m gap_engine.enqueue ingest --market bookkeeping-qc --adapter hn --query "quickbooks export"
python -m gap_engine.enqueue ingest --market bookkeeping-qc --adapter app_store \
    --app-id 584606479 --country ca --competitor QuickBooks
python -m gap_engine.worker --queue all --drain     # every stage until idle, then exit
```

For continuous operation run one long-lived worker per queue
(`--queue ingest`, `normalize`, `embed`, `extract`, `cluster`, `score`) and
schedule the ingest commands. Workers return jobs stuck in `running` to
`retry` at startup (`--stale-minutes`, default 15).

### Owner judgement

```sql
INSERT INTO gap_cluster_assessments (gap_cluster_id, feasibility_score, defensibility_score, incumbent_fix_penalty, note)
VALUES (42, 0.8, 0.6, 0, 'MVP is a CSV exporter; incumbents ignore Québec firms')
ON CONFLICT (gap_cluster_id) DO UPDATE SET feasibility_score = EXCLUDED.feasibility_score,
  defensibility_score = EXCLUDED.defensibility_score, incumbent_fix_penalty = EXCLUDED.incumbent_fix_penalty,
  note = EXCLUDED.note, updated_at = now();
```

Re-score with `python -m gap_engine.enqueue score --market <market>`.

### ContextWard

ContextWard's Gap analysis connector reads `gap_clusters` with the embedded
`gap_scores` row over PostgREST. Point it at this Supabase project URL; no
ContextWard change is needed.

## Tests

```bash
pip install -r requirements.txt pytest
GAP_TEST_DATABASE_URL=postgresql://... pytest tests     # empty throwaway database with pgvector
# or, where sockets are unavailable:
(cd /some/dir && npm install @electric-sql/pglite @electric-sql/pglite-pgvector)
PGLITE_NODE_DIR=/some/dir pytest tests
```

Unit tests run without a database. Integration tests apply every migration,
run the whole pipeline on `tests/fixtures/reviews.jsonl` with a stub LLM and
hash embeddings, and check the exact columns ContextWard selects.

## Setup

```bash
cd python
pip install -r requirements.txt
export DATABASE_URL="postgresql://..."   # from Supabase project settings
export LLM_API_URL="https://api.openai.com/v1/chat/completions"
export LLM_API_KEY="sk-..."
```

## Running Workers

```bash
# Extraction worker (LLM calls)
python -m gap_engine.worker --queue extract --model gpt-4.1-mini

# Ingestion worker (public sources and file imports)
python -m gap_engine.worker --queue ingest

# Scoring worker
python -m gap_engine.worker --queue score
```

## Running Extraction Standalone

```python
from gap_engine.extract import extract_feedback, ExtractionMonitor

monitor = ExtractionMonitor(model_key="gpt-4.1-mini")
results = extract_feedback(
    review_text="The app crashes every time I try to export a report...",
    model_url="https://api.openai.com/v1/chat/completions",
    api_key="sk-...",
    model_key="gpt-4.1-mini",
    monitor=monitor,
)
print(monitor.summary())
```

## Running Semantic Dedup

```python
from gap_engine.dedup import SemanticDeduplicator

dedup = SemanticDeduplicator(db_conn)
candidates = dedup.find_duplicates(review_id=42)
group_id = dedup.group_duplicates(review_id=42)
```

## Drift Monitoring

```python
from gap_engine.monitor import check_drift, get_cost_summary

alerts = check_drift(db_conn, model_key="gpt-4.1-mini")
for a in alerts:
    print(f"⚠ {a}")

summary = get_cost_summary(db_conn, days=7)
print(f"7-day total cost: ${summary['grand_total_cost']:.2f}")
```

## Queue Options

### Postgres SKIP LOCKED (default, no external deps)
Workers poll `claim_extraction_jobs()` / `claim_queue_jobs()` which use
`FOR UPDATE SKIP LOCKED` for atomic, concurrent-safe job claiming.

### Celery + Redis
```python
# celery_app.py
from celery import Celery
app = Celery("gap_engine", broker="redis://localhost:6379/0",
             backend="db+postgresql://user:pass@host/db")
app.conf.task_routes = {"gap_engine.extract": {"queue": "extract"}}
app.conf.task_acks_late = True
app.conf.task_reject_on_worker_lost = True
```
Run: `celery -A celery_app worker -Q extract -l info --concurrency=4`

### BullMQ + Redis (TypeScript)
```typescript
import { Queue, Worker } from 'bullmq';
const connection = new IORedis('redis://localhost:6379');
const extractQueue = new Queue('extract', { connection });
new Worker('extract', async (job) => { /* ... */ }, { connection, concurrency: 4 });
```

## Scoring Formula

```
GapScore = 100 × (0.20F + 0.20I + 0.15B + 0.15W + 0.10P + 0.05S + 0.10Fe + 0.05D - penalty)
```

| Component | Weight | Description |
|-----------|--------|-------------|
| F (frequency) | 0.20 | Distinct documents mentioning the gap, log-scaled |
| I (intensity) | 0.20 | Severity: time loss, money loss, risk, compliance |
| B (breadth) | 0.15 | Competitors implicated / 5 |
| W (willingness to pay) | 0.15 | Explicit payment/switching intent |
| P (workaround pressure) | 0.10 | Manual hacks, spreadsheets, scripts |
| S (segment clarity) | 0.05 | Specific recurring buyer segment |
| Fe (feasibility) | 0.10 | Team's ability to build an MVP |
| D (defensibility) | 0.05 | How hard for incumbents to copy |

Penalties: -10 to -20 for easy incumbent fixes, -10 for single-source monoculture.
