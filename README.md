# Trial Optimizer

Trial Optimizer is an evidence-first foundation for learning from prior clinical programs. It
starts with ClinicalTrials.gov, connects public regulatory, literature, corporate, and safety
evidence, and accepts analog relationships from Convoke Bio without treating any one source as
ground truth.

The core design deliberately separates four things that are often conflated:

1. **Registry state**: what a sponsor submitted to a trial registry, with every retrieved version.
2. **Observed events**: endpoint readouts, discontinuations, regulatory actions, approvals, and
   safety signals.
3. **Evidence claims**: source-linked statements supporting or contradicting an interpretation.
4. **Assessment**: a reviewable success/failure label and possible causal factors, each with
   confidence and provenance.

`COMPLETED` therefore never means "successful," and `TERMINATED` never supplies a causal reason by
itself.

## What is included

- A PostgreSQL schema for source snapshots, trials, assets, programs, analogs, outcomes, causal
  factors, and review queues.
- A ClinicalTrials.gov API v2 client and normalized importer.
- A generic Convoke analog CSV importer. It preserves Convoke's labels and scores while entity
  resolution is pending.
- A Convoke Program Tracker snapshot importer covering active, inactive, probable-inactive, and
  discontinued programs plus their linked trials.
- A Bright Data snapshot downloader/importer for public pages that lack a stable first-party API.
- A browser dashboard with trial search and an evidence-linked trial design recommender.
- Source and outcome-taxonomy guidance in [`docs/`](docs/).

## Quick start

```bash
cp .env.example .env
docker compose up -d postgres
uv sync --extra dev
uv run trialopt init-db
uv run trialopt ingest-ctgov --nct-id NCT05838625
uv run trialopt web
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), then enter a drug and disease under **Design a
trial**. The generated brief benchmarks allocation, masking, comparator, enrollment, and primary
endpoints. It cites reviewed successful and failed analogs separately from active trials,
registry-completed trials, and inactive or discontinued programs.

Search and ingest a bounded set of records:

```bash
uv run trialopt ingest-ctgov --query 'AREA[ConditionSearch] "multiple myeloma"' --limit 25
```

Import a Convoke export using the initial interchange contract:

```bash
uv run trialopt ingest-convoke path/to/convoke_analogs.csv
```

Import the JSON envelope returned by Convoke's `query_program_tracker` MCP tool:

```bash
uv run trialopt ingest-convoke-programs data/convoke/programs.json
```

If no path is supplied, the command imports every JSON file under `CONVOKE_SNAPSHOT_DIR`.

For a complete historical snapshot, request the statuses `active`, `inactive`,
`probable_inactive`, and `discontinued`. Convoke MCP authentication stays managed by Codex, so no
Convoke secret is copied into `.env`; the exported response is the auditable boundary. Completed
trial records come from ClinicalTrials.gov and remain outcome-neutral until reviewed evidence is
accepted.

Import a Bright Data JSON, JSONL, NDJSON, or CSV snapshot already downloaded locally:

```bash
uv run trialopt ingest-web-snapshot data/sponsor_news.jsonl --source-system sponsor_press_release
```

Or download a completed snapshot before importing it:

```bash
uv run trialopt download-brightdata SNAPSHOT_ID data/snapshot.jsonl
```

## Convoke interchange contract

The importer requires `anchor_name` and `analog_name`. It accepts these optional columns:

| Column | Meaning |
|---|---|
| `source_record_id` | Stable Convoke record/link identifier |
| `anchor_type`, `analog_type` | `asset`, `program`, `trial`, or `free_text` |
| `overall_score` | 0-1 similarity score |
| `dimensions_json` | JSON object such as target, modality, indication, biomarker, or design scores |
| `rationale` | Human-readable reason for the analog relationship |
| `source_url` | Link back to Convoke when available |
| `as_of_date` | ISO date for the tracker observation |

This is an adapter boundary, not an assertion that Convoke currently exports exactly these fields.
Once the actual tracker export or API response is available, its fields should be mapped here and
the raw response retained as a source document.

The Program Tracker adapter accepts the MCP response object directly: an `items` array plus the
optional `entity_resolution` array. Each item retains the program status, development stage,
organizations, targets, modalities, routes, linked trials, truncation flag, and observation
provenance.

## Configuration

Copy `.env.example` to `.env`. The CLI and web app load it automatically. `DATABASE_URL` is the
only required setting. `BRIGHT_DATA_API_TOKEN` is required only for direct Bright Data snapshot
downloads. `CONVOKE_SNAPSHOT_DIR`, `TRIALOPT_HOST`, and `TRIALOPT_PORT` are optional local workflow
settings; Convoke MCP itself does not require an app-managed API key.

## Architecture notes

- Use direct official APIs or bulk files when they exist. Reserve Bright Data for sponsor press
  releases, investor pages, conference pages, and other public web sources without stable APIs.
- Store raw payloads or immutable object-storage URIs before normalization.
- Keep a history of ClinicalTrials.gov records to detect endpoint, enrollment, arm, eligibility,
  and status changes.
- Run entity resolution as a scored, reviewable process. Never merge assets only because names
  match after punctuation removal.
- Do not use spontaneous adverse-event reports to infer incidence or causation; they are signal
  evidence only.

See [the architecture](docs/architecture.md), [source catalog](docs/source_catalog.md), and
[outcome/causality model](docs/outcome_and_causality.md) for the implementation roadmap.
