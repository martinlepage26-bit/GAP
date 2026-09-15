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
| `extract.py` | LLM extraction with cost/token tracking, rate limiting, retries |
| `worker.py` | Postgres SKIP LOCKED queue workers + Celery/BullMQ examples |
| `dedup.py` | 4-stage dedup pipeline: exact → text → thread → pgvector semantic |
| `monitor.py` | Cost/token/rate-limit monitoring + drift detection + alerts |

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

# Ingestion worker (scrapers)
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
