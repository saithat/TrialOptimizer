# Trial Optimizer

Trial Optimizer is an evidence-first workspace for learning from prior clinical programs and
reviewing trial designs. It combines two applications:

- **Optimizer dashboard** — a FastAPI and PostgreSQL application for registry ingestion,
  source-linked outcome assessment, analog comparison, and auditable trial-design recommendations.
- **Protocol reviewer** — a React application for reviewing an NCT record or protocol draft,
  marking risky clauses, and tracing each suggestion to trial analogs and supporting sources.

The system keeps registry state, observed events, evidence claims, and human assessment separate.
`COMPLETED` therefore never means "successful," and `TERMINATED` never supplies a causal reason by
itself.

## Repository layout

```text
frontend/                    React and Vite protocol reviewer
src/trial_optimizer/         Python package, API, dashboard, and ingestion clients
sql/                         PostgreSQL schema
tests/                       Python unit and API-contract tests
docs/                        Architecture, source, and outcome guidance
```

## Run the complete application

Prerequisites are Python 3.11 or newer, `uv`, Node.js, npm, Docker, and Docker Compose.

Build the protocol reviewer first:

```bash
cd frontend
npm ci
npm run build
cd ..
```

Then start the database and Python application:

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --extra dev
uv run trialopt init-db
uv run trialopt ingest-ctgov --nct-id NCT05838625
uv run trialopt web
```

Open:

- [http://127.0.0.1:8000](http://127.0.0.1:8000) for the optimizer dashboard.
- [http://127.0.0.1:8000/review/](http://127.0.0.1:8000/review/) for protocol review.

The reviewer build is optional. If it has not been built, `/review/` returns a clear setup error
while the optimizer dashboard and API continue to work.

## Frontend development

Run FastAPI on port 8000, then start Vite in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://127.0.0.1:5173/review/](http://127.0.0.1:5173/review/). Vite forwards `/api`
requests to FastAPI, and FastAPI performs the server-side request to the official
ClinicalTrials.gov API. Production uses the same `/api/clinicaltrials/{nct_id}` contract and does
not depend on a browser CORS workaround.

## Data ingestion

Search and ingest a bounded set of ClinicalTrials.gov records:

```bash
uv run trialopt ingest-ctgov --query 'AREA[ConditionSearch] "multiple myeloma"' --limit 25
```

Import a Convoke analog CSV:

```bash
uv run trialopt ingest-convoke path/to/convoke_analogs.csv
```

Import the JSON envelope returned by Convoke's `query_program_tracker` MCP tool:

```bash
uv run trialopt ingest-convoke-programs data/convoke/programs.json
```

Import a Bright Data JSON, JSONL, NDJSON, or CSV snapshot already downloaded locally:

```bash
uv run trialopt ingest-web-snapshot data/sponsor_news.jsonl --source-system sponsor_press_release
```

Or download a completed snapshot before importing it:

```bash
uv run trialopt download-brightdata SNAPSHOT_ID data/snapshot.jsonl
```

## Evidence and AI boundaries

The protocol reviewer includes a clearly labeled synthetic protocol, scenario outputs, and
qualitative analogy judgments. Live NCT fields come from the official registry, but a registry
status or sponsor-submitted explanation is not treated as a causal outcome assessment. A stopped
trial remains distinct from a completed trial that missed its prespecified endpoint.

The optimizer can optionally add OpenAI synthesis to its deterministic benchmark. The model must
use supplied citation identifiers, cannot replace deterministic design fields or outcome labels,
and falls back safely if provider or citation validation fails. Each attempt is recorded in
`llm_recommendation_run` with its evidence snapshot, structured response, token usage, and status.

Convoke-derived records are excluded from OpenAI requests by default. Set
`OPENAI_INCLUDE_CONVOKE_CONTEXT=true` only after confirming that external processing is permitted.
The application never requires a Convoke secret in `.env`; exported responses are the auditable
interchange boundary.

This is research workflow software, not clinical, statistical, regulatory, or medical advice. Do
not paste confidential or patient-identifiable information into the prototype reviewer.

## Configuration

`DATABASE_URL` is the only required backend setting. `BRIGHT_DATA_API_TOKEN` is needed only for
direct Bright Data downloads. `OPENAI_API_KEY` enables optional AI synthesis. The model, reasoning
effort, timeout, retry count, and output-token limit can also be configured in `.env`.

## Validation

```bash
uv run pytest -q
uv run ruff check .
cd frontend
npm ci
npm run build
```

See [the architecture](docs/architecture.md), [source catalog](docs/source_catalog.md), and
[outcome/causality model](docs/outcome_and_causality.md) for the implementation roadmap.
