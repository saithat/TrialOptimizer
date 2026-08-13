# Database schemas

Trial Optimizer uses PostgreSQL database `trialopt` and application schema `trialopt`. Connections use the search path `trialopt, public`.

The canonical executable definition is [`sql/001_initial.sql`](sql/001_initial.sql). Initialize or update a database with:

```bash
uv run trialopt init-db
```

## Schema overview

| Area | Tables |
| --- | --- |
| Ingestion and provenance | `ingestion_run`, `source_document`, `source_observation` |
| Core entities | `organization`, `organization_alias`, `indication`, `asset`, `asset_name`, `asset_identifier`, `asset_target` |
| Trial registry | `trial`, `trial_version`, `trial_sponsor`, `trial_condition`, `trial_arm`, `trial_intervention`, `outcome_measure`, `trial_reference`, `trial_site` |
| Programs and evidence | `development_program`, `program_trial`, `program_event`, `regulatory_action`, `evidence_claim`, `outcome_assessment`, `outcome_assessment_evidence`, `causal_factor`, `causal_factor_evidence`, `analog_relationship` |
| Imported program snapshots | `convoke_program_snapshot` |
| Recommendation and review audit | `llm_recommendation_run`, `protocol_review_run`, `protocol_review_decision` |
| Entity resolution | `entity_resolution_candidate` |

## Views

| View | Purpose |
| --- | --- |
| `current_trial_summary` | Current trial records joined to the active registry version and its source metadata |
| `latest_accepted_outcome` | Latest accepted outcome assessment for each trial/program and assessment scope |

## Executable PostgreSQL definition

The following is the complete schema definition currently used by the application.

```sql
BEGIN;

CREATE SCHEMA IF NOT EXISTS trialopt;
SET search_path TO trialopt, public;

CREATE TABLE IF NOT EXISTS ingestion_run (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system       text NOT NULL,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    status              text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
    cursor              text,
    records_seen        integer NOT NULL DEFAULT 0,
    records_inserted    integer NOT NULL DEFAULT 0,
    error_message       text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS source_document (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ingestion_run_id    bigint REFERENCES ingestion_run(id),
    source_system       text NOT NULL,
    source_record_type  text NOT NULL,
    locator             text NOT NULL,
    canonical_url       text,
    published_at        timestamptz,
    source_updated_at   timestamptz,
    retrieved_at        timestamptz NOT NULL DEFAULT now(),
    content_type        text,
    content_sha256      text NOT NULL CHECK (length(content_sha256) = 64),
    raw_payload         jsonb,
    object_uri          text,
    license_uri         text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (raw_payload IS NOT NULL OR object_uri IS NOT NULL),
    UNIQUE (source_system, locator, content_sha256)
);

CREATE INDEX IF NOT EXISTS source_document_locator_idx
    ON source_document (source_system, locator, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS source_document_payload_gin
    ON source_document USING gin (raw_payload);

CREATE TABLE IF NOT EXISTS source_observation (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_document_id  bigint NOT NULL REFERENCES source_document(id) ON DELETE CASCADE,
    ingestion_run_id    bigint REFERENCES ingestion_run(id) ON DELETE SET NULL,
    observed_at         timestamptz NOT NULL DEFAULT now(),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_document_id, ingestion_run_id)
);

CREATE INDEX IF NOT EXISTS source_observation_timeline_idx
    ON source_observation (source_document_id, observed_at DESC);

INSERT INTO source_observation (source_document_id, ingestion_run_id, observed_at)
SELECT id, ingestion_run_id, retrieved_at
FROM source_document
WHERE ingestion_run_id IS NOT NULL
ON CONFLICT (source_document_id, ingestion_run_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS organization (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preferred_name      text NOT NULL,
    normalized_name     text NOT NULL,
    organization_type   text,
    country_code        text,
    cik                 text,
    lei                 text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_name)
);

CREATE TABLE IF NOT EXISTS organization_alias (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id     bigint NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
    alias               text NOT NULL,
    normalized_alias    text NOT NULL,
    source_document_id  bigint REFERENCES source_document(id),
    UNIQUE (organization_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS organization_alias_lookup_idx
    ON organization_alias (normalized_alias);

CREATE TABLE IF NOT EXISTS indication (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preferred_name      text NOT NULL,
    normalized_name     text NOT NULL UNIQUE,
    mondo_id            text,
    mesh_id             text,
    ncit_id             text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS asset (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preferred_name      text NOT NULL,
    normalized_name     text NOT NULL,
    asset_type          text NOT NULL DEFAULT 'unknown'
                        CHECK (asset_type IN (
                            'small_molecule', 'biologic', 'vaccine', 'cell_therapy',
                            'gene_therapy', 'device', 'combination', 'behavioral', 'other', 'unknown'
                        )),
    parent_asset_id     bigint REFERENCES asset(id),
    canonical_smiles    text,
    inchikey            text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS asset_normalized_name_idx ON asset (normalized_name);
CREATE UNIQUE INDEX IF NOT EXISTS asset_inchikey_uniq
    ON asset (inchikey) WHERE inchikey IS NOT NULL;

CREATE TABLE IF NOT EXISTS asset_name (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id            bigint NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    name                text NOT NULL,
    normalized_name     text NOT NULL,
    name_type           text NOT NULL DEFAULT 'alias',
    source_document_id  bigint REFERENCES source_document(id),
    UNIQUE (asset_id, normalized_name)
);

CREATE INDEX IF NOT EXISTS asset_name_lookup_idx ON asset_name (normalized_name);

CREATE TABLE IF NOT EXISTS asset_identifier (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id            bigint NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    namespace           text NOT NULL,
    identifier          text NOT NULL,
    source_document_id  bigint REFERENCES source_document(id),
    UNIQUE (namespace, identifier)
);

CREATE TABLE IF NOT EXISTS asset_target (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id            bigint NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
    target_name         text NOT NULL,
    target_identifier   text,
    action_type         text,
    mechanism_text      text,
    confidence          numeric(4,3) CHECK (confidence BETWEEN 0 AND 1),
    source_document_id  bigint REFERENCES source_document(id)
);

CREATE TABLE IF NOT EXISTS trial (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nct_id                  text NOT NULL UNIQUE CHECK (nct_id ~ '^NCT[0-9]{8}$'),
    brief_title             text,
    official_title          text,
    overall_status          text,
    why_stopped             text,
    study_type              text,
    phases                  text[] NOT NULL DEFAULT '{}'::text[],
    allocation              text,
    intervention_model      text,
    masking                 text,
    primary_purpose         text,
    enrollment_count        integer,
    enrollment_type         text,
    sex                     text,
    minimum_age             text,
    maximum_age             text,
    healthy_volunteers      boolean,
    start_date              date,
    primary_completion_date date,
    completion_date         date,
    last_update_posted      date,
    has_results             boolean NOT NULL DEFAULT false,
    current_source_document_id bigint REFERENCES source_document(id),
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS trial_status_phase_idx ON trial (overall_status, phases);
CREATE INDEX IF NOT EXISTS trial_completion_idx ON trial (primary_completion_date);

ALTER TABLE trial ADD COLUMN IF NOT EXISTS allocation text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS intervention_model text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS masking text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS primary_purpose text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS sex text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS minimum_age text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS maximum_age text;
ALTER TABLE trial ADD COLUMN IF NOT EXISTS healthy_volunteers boolean;

CREATE TABLE IF NOT EXISTS trial_version (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_id            bigint NOT NULL REFERENCES trial(id) ON DELETE CASCADE,
    source_document_id  bigint NOT NULL REFERENCES source_document(id),
    version_number      integer,
    record_hash         text NOT NULL CHECK (length(record_hash) = 64),
    source_updated_at   timestamptz,
    observed_at         timestamptz NOT NULL DEFAULT now(),
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_to            timestamptz,
    UNIQUE (trial_id, record_hash),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS trial_version_one_current_idx
    ON trial_version (trial_id) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS trial_sponsor (
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    organization_id     bigint NOT NULL REFERENCES organization(id),
    sponsor_role        text NOT NULL CHECK (sponsor_role IN ('lead', 'collaborator', 'responsible_party')),
    PRIMARY KEY (trial_version_id, organization_id, sponsor_role)
);

CREATE TABLE IF NOT EXISTS trial_condition (
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    indication_id       bigint NOT NULL REFERENCES indication(id),
    source_name         text NOT NULL,
    PRIMARY KEY (trial_version_id, indication_id, source_name)
);

CREATE TABLE IF NOT EXISTS trial_arm (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    label               text NOT NULL,
    arm_type            text,
    description         text,
    UNIQUE (trial_version_id, label)
);

CREATE TABLE IF NOT EXISTS trial_intervention (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    asset_id            bigint REFERENCES asset(id),
    intervention_type   text,
    source_name         text NOT NULL,
    normalized_name     text NOT NULL,
    description         text,
    arm_labels          text[] NOT NULL DEFAULT '{}'::text[],
    other_names         text[] NOT NULL DEFAULT '{}'::text[],
    UNIQUE (trial_version_id, intervention_type, source_name)
);

CREATE INDEX IF NOT EXISTS trial_intervention_name_idx
    ON trial_intervention (normalized_name);

CREATE TABLE IF NOT EXISTS outcome_measure (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    outcome_type        text NOT NULL CHECK (outcome_type IN ('primary', 'secondary', 'other')),
    ordinal             integer NOT NULL,
    title               text NOT NULL,
    description         text,
    time_frame          text,
    UNIQUE (trial_version_id, outcome_type, ordinal)
);

CREATE TABLE IF NOT EXISTS trial_reference (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    reference_type      text NOT NULL,
    pmid                text,
    doi                 text,
    citation            text,
    url                 text
);

CREATE INDEX IF NOT EXISTS trial_reference_pmid_idx
    ON trial_reference (pmid) WHERE pmid IS NOT NULL;

CREATE TABLE IF NOT EXISTS trial_site (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_version_id    bigint NOT NULL REFERENCES trial_version(id) ON DELETE CASCADE,
    facility            text,
    city                text,
    state               text,
    postal_code         text,
    country             text,
    latitude            numeric,
    longitude           numeric,
    recruitment_status  text
);

CREATE TABLE IF NOT EXISTS development_program (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id            bigint NOT NULL REFERENCES asset(id),
    indication_id       bigint REFERENCES indication(id),
    lead_organization_id bigint REFERENCES organization(id),
    program_name        text,
    status              text NOT NULL DEFAULT 'unknown',
    highest_phase       text,
    first_observed_at   timestamptz,
    last_observed_at    timestamptz,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS development_program_lookup_idx
    ON development_program (asset_id, indication_id, lead_organization_id);

CREATE TABLE IF NOT EXISTS program_trial (
    program_id          bigint NOT NULL REFERENCES development_program(id) ON DELETE CASCADE,
    trial_id            bigint NOT NULL REFERENCES trial(id) ON DELETE CASCADE,
    relationship_type   text NOT NULL DEFAULT 'evaluates',
    confidence          numeric(4,3) CHECK (confidence BETWEEN 0 AND 1),
    source_document_id  bigint REFERENCES source_document(id),
    PRIMARY KEY (program_id, trial_id)
);

CREATE TABLE IF NOT EXISTS program_event (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    program_id          bigint NOT NULL REFERENCES development_program(id) ON DELETE CASCADE,
    event_type          text NOT NULL,
    event_date          date,
    event_text          text,
    source_document_id  bigint NOT NULL REFERENCES source_document(id),
    confidence          numeric(4,3) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS program_event_timeline_idx
    ON program_event (program_id, event_date);

CREATE TABLE IF NOT EXISTS regulatory_action (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    program_id          bigint REFERENCES development_program(id),
    asset_id            bigint REFERENCES asset(id),
    organization_id     bigint REFERENCES organization(id),
    authority           text NOT NULL,
    jurisdiction        text,
    application_number  text,
    action_type         text NOT NULL,
    action_date         date,
    indication_text     text,
    reason_text         text,
    source_document_id  bigint NOT NULL REFERENCES source_document(id),
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (program_id IS NOT NULL OR asset_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS regulatory_action_application_idx
    ON regulatory_action (authority, application_number);

CREATE TABLE IF NOT EXISTS evidence_claim (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_id            bigint REFERENCES trial(id),
    program_id          bigint REFERENCES development_program(id),
    asset_id            bigint REFERENCES asset(id),
    predicate           text NOT NULL,
    object_text         text,
    numeric_value       numeric,
    unit                text,
    polarity            text NOT NULL DEFAULT 'supports'
                        CHECK (polarity IN ('supports', 'contradicts', 'neutral')),
    evidence_level      text NOT NULL DEFAULT 'unrated'
                        CHECK (evidence_level IN (
                            'regulatory', 'registry_result', 'peer_reviewed', 'conference',
                            'company_disclosure', 'public_filing', 'secondary', 'inference', 'unrated'
                        )),
    confidence          numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    assertion_date      date,
    extracted_by        text NOT NULL,
    extraction_version  text,
    evidence_quote      text,
    evidence_location   text,
    source_document_id  bigint NOT NULL REFERENCES source_document(id),
    review_status       text NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending', 'accepted', 'rejected', 'superseded')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(trial_id, program_id, asset_id) = 1),
    CHECK (object_text IS NOT NULL OR numeric_value IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS evidence_claim_trial_idx ON evidence_claim (trial_id, predicate);
CREATE INDEX IF NOT EXISTS evidence_claim_program_idx ON evidence_claim (program_id, predicate);

CREATE TABLE IF NOT EXISTS outcome_assessment (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trial_id                bigint REFERENCES trial(id),
    program_id              bigint REFERENCES development_program(id),
    assessment_scope        text NOT NULL,
    assessment_date         date,
    evidence_cutoff_date    date NOT NULL,
    conduct_status          text,
    endpoint_result         text CHECK (endpoint_result IN (
                                'met', 'partially_met', 'not_met', 'harm',
                                'not_tested', 'inconclusive', 'unknown'
                            )),
    program_disposition     text CHECK (program_disposition IN (
                                'advanced', 'approved', 'continued', 'paused', 'discontinued',
                                'rejected', 'withdrawn', 'ongoing', 'unknown'
                            )),
    outcome                 text NOT NULL CHECK (outcome IN (
                                'success', 'partial_success', 'failure',
                                'inconclusive', 'ongoing', 'unknown'
                            )),
    confidence              numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale               text,
    assessed_by             text NOT NULL,
    assessment_version      text NOT NULL,
    review_status           text NOT NULL DEFAULT 'pending'
                            CHECK (review_status IN ('pending', 'accepted', 'rejected', 'superseded')),
    created_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(trial_id, program_id) = 1)
);

CREATE INDEX IF NOT EXISTS outcome_assessment_cutoff_idx
    ON outcome_assessment (evidence_cutoff_date, outcome);

CREATE TABLE IF NOT EXISTS outcome_assessment_evidence (
    outcome_assessment_id bigint NOT NULL REFERENCES outcome_assessment(id) ON DELETE CASCADE,
    evidence_claim_id     bigint NOT NULL REFERENCES evidence_claim(id),
    relationship          text NOT NULL CHECK (relationship IN ('supports', 'contradicts', 'context')),
    PRIMARY KEY (outcome_assessment_id, evidence_claim_id)
);

CREATE TABLE IF NOT EXISTS causal_factor (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    outcome_assessment_id   bigint NOT NULL REFERENCES outcome_assessment(id) ON DELETE CASCADE,
    category                text NOT NULL CHECK (category IN (
                                'biology', 'efficacy', 'safety', 'dose_exposure',
                                'population_selection', 'endpoint_choice', 'trial_design',
                                'operations', 'manufacturing_quality', 'regulatory',
                                'commercial_strategy', 'external', 'unknown'
                            )),
    factor                  text NOT NULL,
    role                    text NOT NULL CHECK (role IN ('primary', 'contributing', 'contextual')),
    sponsor_stated          boolean NOT NULL DEFAULT false,
    confidence              numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale               text
);

CREATE TABLE IF NOT EXISTS causal_factor_evidence (
    causal_factor_id        bigint NOT NULL REFERENCES causal_factor(id) ON DELETE CASCADE,
    evidence_claim_id       bigint NOT NULL REFERENCES evidence_claim(id),
    relationship            text NOT NULL CHECK (relationship IN ('supports', 'contradicts', 'context')),
    PRIMARY KEY (causal_factor_id, evidence_claim_id)
);

CREATE TABLE IF NOT EXISTS analog_relationship (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system       text NOT NULL,
    source_record_id    text,
    anchor_type         text NOT NULL DEFAULT 'free_text'
                        CHECK (anchor_type IN ('asset', 'program', 'trial', 'free_text')),
    anchor_label        text NOT NULL,
    anchor_asset_id     bigint REFERENCES asset(id),
    anchor_program_id   bigint REFERENCES development_program(id),
    anchor_trial_id     bigint REFERENCES trial(id),
    analog_type         text NOT NULL DEFAULT 'free_text'
                        CHECK (analog_type IN ('asset', 'program', 'trial', 'free_text')),
    analog_label        text NOT NULL,
    analog_asset_id     bigint REFERENCES asset(id),
    analog_program_id   bigint REFERENCES development_program(id),
    analog_trial_id     bigint REFERENCES trial(id),
    overall_score       numeric(6,5) CHECK (overall_score BETWEEN 0 AND 1),
    dimension_scores    jsonb NOT NULL DEFAULT '{}'::jsonb,
    rationale           text,
    asserted_at         date,
    source_document_id  bigint NOT NULL REFERENCES source_document(id),
    resolution_status   text NOT NULL DEFAULT 'unresolved'
                        CHECK (resolution_status IN ('unresolved', 'partially_resolved', 'resolved', 'rejected')),
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS analog_anchor_lookup_idx
    ON analog_relationship (source_system, anchor_label);
CREATE INDEX IF NOT EXISTS analog_scores_gin
    ON analog_relationship USING gin (dimension_scores);

CREATE TABLE IF NOT EXISTS convoke_program_snapshot (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drug_id                 bigint,
    drug_name               text NOT NULL,
    indication_id           bigint,
    indication_name         text NOT NULL,
    development_stage       text,
    program_status          text,
    organizations           jsonb NOT NULL DEFAULT '[]'::jsonb,
    targets                 jsonb NOT NULL DEFAULT '[]'::jsonb,
    modalities              jsonb NOT NULL DEFAULT '[]'::jsonb,
    routes_of_administration jsonb NOT NULL DEFAULT '[]'::jsonb,
    trials                  jsonb NOT NULL DEFAULT '[]'::jsonb,
    trial_count_total       integer,
    trials_truncated        boolean NOT NULL DEFAULT false,
    entity_resolution       jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_document_id      bigint NOT NULL REFERENCES source_document(id),
    observed_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_document_id)
);

CREATE INDEX IF NOT EXISTS convoke_program_drug_idx
    ON convoke_program_snapshot (lower(drug_name));
CREATE INDEX IF NOT EXISTS convoke_program_indication_idx
    ON convoke_program_snapshot (lower(indication_name));
CREATE INDEX IF NOT EXISTS convoke_program_trials_gin
    ON convoke_program_snapshot USING gin (trials);

CREATE TABLE IF NOT EXISTS llm_recommendation_run (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_payload             jsonb NOT NULL,
    evidence_snapshot           jsonb NOT NULL,
    deterministic_recommendation jsonb NOT NULL,
    llm_output                  jsonb,
    provider                    text NOT NULL DEFAULT 'openai',
    model                       text NOT NULL,
    prompt_version              text NOT NULL,
    status                      text NOT NULL CHECK (status IN (
                                    'enhanced', 'fallback', 'validation_failed'
                                )),
    provider_response_id        text,
    included_convoke_context    boolean NOT NULL DEFAULT false,
    input_tokens                integer,
    output_tokens               integer,
    total_tokens                integer,
    error_category              text,
    created_at                  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_recommendation_created_idx
    ON llm_recommendation_run (created_at DESC);

CREATE TABLE IF NOT EXISTS protocol_review_run (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type                 text NOT NULL CHECK (source_type IN ('nct', 'text', 'demo')),
    nct_id                      text,
    title                       text NOT NULL,
    input_snapshot              jsonb NOT NULL,
    evidence_snapshot           jsonb NOT NULL DEFAULT '[]'::jsonb,
    deterministic_findings      jsonb NOT NULL DEFAULT '[]'::jsonb,
    llm_output                  jsonb,
    provider                    text,
    model                       text,
    prompt_version              text,
    status                      text NOT NULL CHECK (status IN (
                                    'rules_only', 'enhanced', 'fallback', 'validation_failed'
                                )),
    provider_response_id        text,
    included_convoke_context    boolean NOT NULL DEFAULT false,
    input_tokens                integer,
    output_tokens               integer,
    total_tokens                integer,
    error_category              text,
    created_at                  timestamptz NOT NULL DEFAULT now(),
    CHECK (nct_id IS NULL OR nct_id ~ '^NCT[0-9]{8}$')
);

CREATE INDEX IF NOT EXISTS protocol_review_created_idx
    ON protocol_review_run (created_at DESC);
CREATE INDEX IF NOT EXISTS protocol_review_nct_idx
    ON protocol_review_run (nct_id, created_at DESC) WHERE nct_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS protocol_review_decision (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    protocol_review_id  bigint NOT NULL REFERENCES protocol_review_run(id) ON DELETE CASCADE,
    finding_id          text NOT NULL,
    decision            text NOT NULL CHECK (decision IN ('accepted', 'rejected', 'team_review')),
    original_text       text,
    replacement_text    text,
    decided_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (protocol_review_id, finding_id)
);

CREATE INDEX IF NOT EXISTS protocol_review_decision_review_idx
    ON protocol_review_decision (protocol_review_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS entity_resolution_candidate (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system       text NOT NULL,
    source_label        text NOT NULL,
    entity_type         text NOT NULL CHECK (entity_type IN ('asset', 'organization', 'indication', 'trial', 'program')),
    candidate_entity_id bigint NOT NULL,
    match_method        text NOT NULL,
    score               numeric(6,5) NOT NULL CHECK (score BETWEEN 0 AND 1),
    features            jsonb NOT NULL DEFAULT '{}'::jsonb,
    review_status       text NOT NULL DEFAULT 'pending'
                        CHECK (review_status IN ('pending', 'accepted', 'rejected')),
    reviewed_by         text,
    reviewed_at         timestamptz,
    UNIQUE (source_system, source_label, entity_type, candidate_entity_id)
);

CREATE OR REPLACE VIEW current_trial_summary AS
SELECT
    t.id,
    t.nct_id,
    t.brief_title,
    t.overall_status,
    t.why_stopped,
    t.phases,
    t.enrollment_count,
    t.start_date,
    t.primary_completion_date,
    t.completion_date,
    t.has_results,
    tv.observed_at AS registry_observed_at,
    sd.source_updated_at,
    sd.canonical_url
FROM trial t
LEFT JOIN trial_version tv
    ON tv.trial_id = t.id AND tv.valid_to IS NULL
LEFT JOIN source_document sd
    ON sd.id = tv.source_document_id;

CREATE OR REPLACE VIEW latest_accepted_outcome AS
SELECT DISTINCT ON (COALESCE(trial_id, -1), COALESCE(program_id, -1), assessment_scope)
    *
FROM outcome_assessment
WHERE review_status = 'accepted'
ORDER BY COALESCE(trial_id, -1), COALESCE(program_id, -1), assessment_scope,
         evidence_cutoff_date DESC, created_at DESC;

COMMIT;
```

