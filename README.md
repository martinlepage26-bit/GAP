# GAP — Market Gap Discovery Engine

GAP turns public reviews and demand signals into structured, ranked evidence about unmet customer needs. It combines a Python processing pipeline with a React dashboard for inspecting gap clusters, duplicate groups, queue status, and extraction costs.

## Engineering overview

| Layer | Implementation |
| --- | --- |
| Dashboard | React, TypeScript, Vite, Tailwind CSS |
| Data | Supabase / PostgreSQL, pgvector, SQL migrations |
| Processing | Python, structured LLM extraction, local multilingual embeddings |
| Reliability | Queue claiming with `SKIP LOCKED`, retries, stale-job recovery, validation and cost tracking |
| Verification | Unit tests and a fixture-based integration pipeline using a stub LLM and hash embeddings |

The pipeline ingests documents, normalizes and deduplicates text, embeds reviews, extracts atomic complaints, clusters related needs, and calculates weighted scores. Feasibility and defensibility include explicit human assessments; ranked gaps are research inputs, not proof of market demand.

## Review the implementation

- [Pipeline architecture, setup and scoring](python/README.md)
- [Pipeline modules](python/gap_engine/)
- [Tests and sample reviews](python/tests/)
- [Database migrations](supabase/migrations/)
- [Dashboard](src/App.tsx)

## Run the dashboard

```sh
npm install
npm run dev
```

Set `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` in a local `.env.local`. Use your own Supabase project with the committed migrations and appropriate database access policies. Never put a service-role key in frontend environment variables.

Available frontend checks:

```sh
npm run lint
npm run typecheck
npm run build
```

## Run the pipeline

Follow [the Python setup guide](python/README.md#setup) for database and model credentials, then enqueue an ingestion job and drain the workers. Integration tests need a throwaway PostgreSQL database with pgvector, or the documented PGlite alternative.

The frontend needs configured data access to display pipeline results. LLM extraction requires a configured provider and may incur charges. This README does not claim a fresh passing test run or a production deployment.
