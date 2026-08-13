# Architecture

## Recommended data flow

```text
Official APIs and bulk files       Convoke tracker       Public web via Bright Data
             |                          |                         |
             +--------------------------+-------------------------+
                                        |
                             immutable source_document
                             + content hash + retrieval time
                                        |
                 +----------------------+----------------------+
                 |                                             |
        source-specific parsing                         claim extraction
                 |                                             |
        normalized trial graph                         evidence_claim
                 |                                             |
                 +----------------------+----------------------+
                                        |
                         reviewed outcome_assessment
                         + causal_factor + confidence
                                        |
                       analog cohorts / design comparisons
```

## Storage strategy

PostgreSQL is the system of record. JSONB is used at the source boundary, while commonly queried
facts are relational. For production-scale PDF/HTML collections, put original bytes in immutable
object storage and store the URI, SHA-256 hash, MIME type, retrieval timestamp, and source URL in
`source_document`.

The schema is intentionally bitemporal at the source layer:

- `source_document.retrieved_at` records the first retrieval of immutable content, while
  `source_observation` records every ingestion run that saw it.
- `source_updated_at` records the source's own update time when available.
- `trial_version.valid_from` / `valid_to` record when a registry version was the latest version we
  knew about.

This is necessary to detect design changes and avoid future information leaking into retrospective
analyses.

## Entity model

- **Asset**: molecular entity, biologic, cell/gene therapy, device, or combination. Names and public
  identifiers are aliases, not the identity itself.
- **Program**: an asset + indication + sponsor development effort. Outcomes belong primarily here.
- **Trial**: a study record. One trial can involve multiple assets, arms, and indications.
- **Analog relationship**: directed relationship from an anchor to a candidate analog, with
  dimension scores and the source that asserted it.

Candidate matches should be scored using multiple dimensions:

1. molecular target and mechanism;
2. modality and molecule class;
3. indication and disease setting;
4. biomarker and line of therapy;
5. route, schedule, combination regimen, and exposure;
6. development stage and regulatory path;
7. trial design, endpoint, comparator, and population.

Convoke is one provider of analog assertions. A reproducible in-house model can add assertions with
`source_system = 'internal_model'`; do not overwrite Convoke's links.

## Pipeline stages

1. **Land** raw, hashed, timestamped source material.
2. **Parse** source-specific structures without making outcome judgments.
3. **Resolve** sponsors, assets, indications, identifiers, and trial/program links.
4. **Extract claims** with a verbatim evidence span or structured field location.
5. **Assess** endpoint outcome, program disposition, and causal factors.
6. **Review** contradictions, low-confidence matches, and high-impact causal assessments.
7. **Serve** analog cohorts, design comparisons, timelines, and model-ready feature snapshots.

## Protocol review flow

The protocol reviewer is part of the same FastAPI application and PostgreSQL database as the trial
dashboard. For an NCT review, the browser asks FastAPI for the current ClinicalTrials.gov record and
runs the local rule checks. It then submits the structured sections and findings to
`/api/protocol-reviews`. The backend retrieves matching saved trials, runs optional structured model
analysis, validates every model citation and quoted protocol span, and saves the request, evidence
snapshot, output, and later review decisions. If the database or model is unavailable, the local
rule findings remain usable.

## Recommended production services

- PostgreSQL for the normalized/evidence store.
- S3-compatible immutable object storage for PDFs, HTML, and large API snapshots.
- A scheduler such as Dagster, Prefect, or managed cron for daily/weekly refreshes.
- A queue for OCR, PDF parsing, entity-resolution, and review tasks.
- dbt or versioned SQL transforms for stable analytic feature tables.

The first implementation does not choose a cloud vendor or orchestration system because those
choices depend on deployment and volume. The schema and adapters remain portable.
