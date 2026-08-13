# Trial Optimizer

Trial Optimizer stores clinical trial evidence, compares study designs, and reviews protocols. Its
dashboard and protocol reviewer use the same backend and PostgreSQL database:

- **Trials and recommendations** — registry ingestion, source-linked outcome assessment, similar
  trial comparison, and trial-design recommendations.
- **Protocol review** — review an NCT record or protocol draft against saved trial records and
  supporting references, with optional citation-checked model analysis.

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

The dashboard compares the same drug across indications directly from saved Program Tracker
records, so it does not require the older analog CSV import. The recommendation view derives
related-disease cohorts from the latest cached Convoke program for each drug and indication. It
prioritizes the same drug in another indication, then shared
targets, and shows compact summaries plus ClinicalTrials.gov links for the tracker's linked trials.
These are transparent Trial Optimizer groupings—not Convoke-authored similarity scores or claims
that clinical outcomes transfer between diseases. When the tracker truncates a trial list, the UI
shows both the returned subset and the reported total. No separate analog CSV import is required;
the cohorts are rebuilt from the cached Program Tracker records for each recommendation request.

Import a Bright Data JSON, JSONL, NDJSON, or CSV snapshot already downloaded locally:

```bash
uv run trialopt ingest-web-snapshot data/sponsor_news.jsonl --source-system sponsor_press_release
```

Or download a completed snapshot before importing it:

```bash
uv run trialopt download-brightdata SNAPSHOT_ID data/snapshot.jsonl
```

## Evidence and AI boundaries

The protocol reviewer includes a clearly labeled sample protocol. Live NCT fields come from the
official registry. Rule-based checks run first, then the backend retrieves relevant saved trial
records and can add citation-checked model findings. Reviews, evidence snapshots, and decisions are
saved in PostgreSQL. A registry status or sponsor-submitted explanation is not treated as a causal
outcome assessment. A stopped trial remains distinct from a completed trial that missed its
prespecified endpoint.

The optimizer and protocol reviewer can optionally add OpenAI analysis after their deterministic
checks. The model must use supplied citation identifiers, cannot replace registry fields or outcome
labels, and falls back safely if provider or citation validation fails. Recommendation attempts are
recorded in `llm_recommendation_run`; protocol reviews and user decisions are recorded in
`protocol_review_run` and `protocol_review_decision` with their evidence snapshots and model usage.
Trial-design results render before the optional model call finishes; the AI summary runs separately
and appears when it is ready. Immediate review questions remain visible, while non-duplicate model
questions are added to the same panel with a saved-evidence citation or an evidence-gap label.

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
