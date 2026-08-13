from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from trial_optimizer.normalization import (
    TrialRecord,
    as_utc_datetime,
    content_hash,
    normalize_name,
    parse_date,
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "sql" / "001_initial.sql"


def init_database(database_url: str) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(sql)


def _doi_from_citation(citation: str | None) -> str | None:
    if not citation:
        return None
    match = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", citation, flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;)") if match else None


class Repository:
    def __init__(self, connection: Connection[Any]) -> None:
        self.connection = connection
        self.connection.execute("SET search_path TO trialopt, public")

    @classmethod
    def connect(cls, database_url: str) -> Repository:
        return cls(psycopg.connect(database_url))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.close()

    def start_run(self, source_system: str, metadata: dict[str, Any] | None = None) -> int:
        row = self.connection.execute(
            """
            INSERT INTO ingestion_run (source_system, metadata)
            VALUES (%s, %s)
            RETURNING id
            """,
            (source_system, Jsonb(metadata or {})),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def finish_run(self, run_id: int, *, seen: int, inserted: int) -> None:
        self.connection.execute(
            """
            UPDATE ingestion_run
            SET finished_at = now(), status = 'succeeded', records_seen = %s, records_inserted = %s
            WHERE id = %s
            """,
            (seen, inserted, run_id),
        )

    def land_source_document(
        self,
        *,
        source_system: str,
        source_record_type: str,
        locator: str,
        raw_payload: dict[str, Any],
        canonical_url: str | None = None,
        source_updated_at: datetime | None = None,
        published_at: datetime | None = None,
        ingestion_run_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        digest = content_hash(raw_payload)
        row = self.connection.execute(
            """
            INSERT INTO source_document (
                ingestion_run_id, source_system, source_record_type, locator, canonical_url,
                published_at, source_updated_at, content_type, content_sha256, raw_payload, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'application/json', %s, %s, %s)
            ON CONFLICT (source_system, locator, content_sha256)
            DO UPDATE SET metadata = source_document.metadata || EXCLUDED.metadata
            RETURNING id
            """,
            (
                ingestion_run_id,
                source_system,
                source_record_type,
                locator,
                canonical_url,
                published_at,
                source_updated_at,
                digest,
                Jsonb(raw_payload),
                Jsonb(metadata or {}),
            ),
        ).fetchone()
        assert row is not None
        source_document_id = int(row[0])
        self.connection.execute(
            """
            INSERT INTO source_observation (source_document_id, ingestion_run_id, metadata)
            VALUES (%s, %s, %s)
            ON CONFLICT (source_document_id, ingestion_run_id) DO NOTHING
            """,
            (source_document_id, ingestion_run_id, Jsonb(metadata or {})),
        )
        return source_document_id

    def ingest_ctgov(self, record: TrialRecord, *, ingestion_run_id: int | None = None) -> bool:
        source_updated_at = as_utc_datetime(record.last_update_posted)
        source_document_id = self.land_source_document(
            source_system="clinicaltrials.gov",
            source_record_type="study",
            locator=record.nct_id,
            canonical_url=f"https://clinicaltrials.gov/study/{record.nct_id}",
            source_updated_at=source_updated_at,
            ingestion_run_id=ingestion_run_id,
            raw_payload=record.raw,
            metadata={"api_version": "v2"},
        )

        trial_row = self.connection.execute(
            """
            INSERT INTO trial (
                nct_id, brief_title, official_title, overall_status, why_stopped, study_type,
                phases, allocation, intervention_model, masking, primary_purpose,
                enrollment_count, enrollment_type, sex, minimum_age, maximum_age,
                healthy_volunteers, start_date, primary_completion_date, completion_date,
                last_update_posted, has_results, current_source_document_id
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (nct_id) DO UPDATE SET
                brief_title = EXCLUDED.brief_title,
                official_title = EXCLUDED.official_title,
                overall_status = EXCLUDED.overall_status,
                why_stopped = EXCLUDED.why_stopped,
                study_type = EXCLUDED.study_type,
                phases = EXCLUDED.phases,
                allocation = EXCLUDED.allocation,
                intervention_model = EXCLUDED.intervention_model,
                masking = EXCLUDED.masking,
                primary_purpose = EXCLUDED.primary_purpose,
                enrollment_count = EXCLUDED.enrollment_count,
                enrollment_type = EXCLUDED.enrollment_type,
                sex = EXCLUDED.sex,
                minimum_age = EXCLUDED.minimum_age,
                maximum_age = EXCLUDED.maximum_age,
                healthy_volunteers = EXCLUDED.healthy_volunteers,
                start_date = EXCLUDED.start_date,
                primary_completion_date = EXCLUDED.primary_completion_date,
                completion_date = EXCLUDED.completion_date,
                last_update_posted = EXCLUDED.last_update_posted,
                has_results = EXCLUDED.has_results,
                current_source_document_id = EXCLUDED.current_source_document_id,
                updated_at = now()
            RETURNING id
            """,
            (
                record.nct_id,
                record.brief_title,
                record.official_title,
                record.overall_status,
                record.why_stopped,
                record.study_type,
                list(record.phases),
                record.allocation,
                record.intervention_model,
                record.masking,
                record.primary_purpose,
                record.enrollment_count,
                record.enrollment_type,
                record.sex,
                record.minimum_age,
                record.maximum_age,
                record.healthy_volunteers,
                record.start_date,
                record.primary_completion_date,
                record.completion_date,
                record.last_update_posted,
                record.has_results,
                source_document_id,
            ),
        ).fetchone()
        assert trial_row is not None
        trial_id = int(trial_row[0])

        digest = content_hash(record.raw)
        existing = self.connection.execute(
            "SELECT id FROM trial_version WHERE trial_id = %s AND record_hash = %s",
            (trial_id, digest),
        ).fetchone()
        if existing:
            return False

        now = datetime.now(UTC)
        self.connection.execute(
            "UPDATE trial_version SET valid_to = %s WHERE trial_id = %s AND valid_to IS NULL",
            (now, trial_id),
        )
        version_row = self.connection.execute(
            """
            INSERT INTO trial_version (
                trial_id, source_document_id, record_hash, source_updated_at, observed_at, valid_from
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (trial_id, source_document_id, digest, source_updated_at, now, now),
        ).fetchone()
        assert version_row is not None
        trial_version_id = int(version_row[0])

        for sponsor in record.sponsors:
            organization_id = self._upsert_organization(
                sponsor.name, sponsor.organization_type, source_document_id
            )
            self.connection.execute(
                """
                INSERT INTO trial_sponsor (trial_version_id, organization_id, sponsor_role)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (trial_version_id, organization_id, sponsor.role),
            )

        for source_name in record.conditions:
            normalized = normalize_name(source_name)
            indication_row = self.connection.execute(
                """
                INSERT INTO indication (preferred_name, normalized_name)
                VALUES (%s, %s)
                ON CONFLICT (normalized_name) DO UPDATE
                    SET preferred_name = indication.preferred_name
                RETURNING id
                """,
                (source_name, normalized),
            ).fetchone()
            assert indication_row is not None
            self.connection.execute(
                """
                INSERT INTO trial_condition (trial_version_id, indication_id, source_name)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (trial_version_id, int(indication_row[0]), source_name),
            )

        for arm in record.arms:
            self.connection.execute(
                """
                INSERT INTO trial_arm (trial_version_id, label, arm_type, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (trial_version_id, label) DO NOTHING
                """,
                (trial_version_id, arm.label, arm.arm_type, arm.description),
            )

        for intervention in record.interventions:
            self.connection.execute(
                """
                INSERT INTO trial_intervention (
                    trial_version_id, intervention_type, source_name, normalized_name,
                    description, arm_labels, other_names
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (trial_version_id, intervention_type, source_name) DO NOTHING
                """,
                (
                    trial_version_id,
                    intervention.intervention_type,
                    intervention.name,
                    normalize_name(intervention.name),
                    intervention.description,
                    list(intervention.arm_labels),
                    list(intervention.other_names),
                ),
            )

        for outcome in record.outcomes:
            self.connection.execute(
                """
                INSERT INTO outcome_measure (
                    trial_version_id, outcome_type, ordinal, title, description, time_frame
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    trial_version_id,
                    outcome.outcome_type,
                    outcome.ordinal,
                    outcome.title,
                    outcome.description,
                    outcome.time_frame,
                ),
            )

        for reference in record.references:
            self.connection.execute(
                """
                INSERT INTO trial_reference (
                    trial_version_id, reference_type, pmid, doi, citation, url
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    trial_version_id,
                    reference.reference_type,
                    reference.pmid,
                    _doi_from_citation(reference.citation),
                    reference.citation,
                    reference.url,
                ),
            )

        for site in record.sites:
            self.connection.execute(
                """
                INSERT INTO trial_site (
                    trial_version_id, facility, city, state, postal_code, country,
                    latitude, longitude, recruitment_status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    trial_version_id,
                    site.facility,
                    site.city,
                    site.state,
                    site.postal_code,
                    site.country,
                    site.latitude,
                    site.longitude,
                    site.recruitment_status,
                ),
            )
        return True

    def ingest_convoke_program(
        self,
        row: dict[str, Any],
        *,
        entity_resolution: list[dict[str, Any]] | None = None,
        locator: str,
        ingestion_run_id: int | None = None,
    ) -> bool:
        payload = {**row, "entity_resolution": entity_resolution or []}
        source_document_id = self.land_source_document(
            source_system="convoke",
            source_record_type="program_tracker",
            locator=locator,
            raw_payload=payload,
            ingestion_run_id=ingestion_run_id,
            metadata={"adapter": "convoke-program-tracker-v1"},
        )
        exists = self.connection.execute(
            "SELECT 1 FROM convoke_program_snapshot WHERE source_document_id = %s",
            (source_document_id,),
        ).fetchone()
        if exists:
            return False
        self.connection.execute(
            """
            INSERT INTO convoke_program_snapshot (
                drug_id, drug_name, indication_id, indication_name, development_stage,
                program_status, organizations, targets, modalities, routes_of_administration,
                trials, trial_count_total, trials_truncated, entity_resolution, source_document_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row.get("drug_id"),
                row["drug_name"],
                row.get("indication_id"),
                row["indication_name"],
                row.get("development_stage"),
                row.get("program_status"),
                Jsonb(row.get("organizations", [])),
                Jsonb(row.get("targets", [])),
                Jsonb(row.get("modalities", [])),
                Jsonb(row.get("routes_of_administration", [])),
                Jsonb(row.get("trials", [])),
                row.get("trial_count_total"),
                bool(row.get("trials_truncated", False)),
                Jsonb(entity_resolution or []),
                source_document_id,
            ),
        )
        return True

    def _upsert_organization(
        self, name: str, organization_type: str | None, source_document_id: int
    ) -> int:
        normalized = normalize_name(name)
        row = self.connection.execute(
            """
            INSERT INTO organization (preferred_name, normalized_name, organization_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (normalized_name) DO UPDATE
                SET organization_type = COALESCE(organization.organization_type, EXCLUDED.organization_type),
                    updated_at = now()
            RETURNING id
            """,
            (name, normalized, organization_type.casefold() if organization_type else None),
        ).fetchone()
        assert row is not None
        organization_id = int(row[0])
        self.connection.execute(
            """
            INSERT INTO organization_alias (
                organization_id, alias, normalized_alias, source_document_id
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (organization_id, normalized_alias) DO NOTHING
            """,
            (organization_id, name, normalized, source_document_id),
        )
        return organization_id

    def ingest_convoke_row(
        self,
        row: dict[str, Any],
        *,
        locator: str,
        ingestion_run_id: int | None = None,
    ) -> bool:
        source_record_id = row.get("source_record_id") or locator
        source_document_id = self.land_source_document(
            source_system="convoke",
            source_record_type="analog_relationship",
            locator=str(source_record_id),
            canonical_url=row.get("source_url") or None,
            raw_payload={key: value for key, value in row.items() if key != "dimensions"},
            ingestion_run_id=ingestion_run_id,
            metadata={"interchange_contract": "convoke-analog-csv-v1"},
        )
        exists = self.connection.execute(
            """
            SELECT 1 FROM analog_relationship
            WHERE source_document_id = %s AND anchor_label = %s AND analog_label = %s
            """,
            (source_document_id, row["anchor_name"], row["analog_name"]),
        ).fetchone()
        if exists:
            return False

        score = float(row["overall_score"]) if row.get("overall_score") else None
        if score is not None and not 0 <= score <= 1:
            raise ValueError(f"overall_score must be between 0 and 1 for {locator}")
        anchor_type = row.get("anchor_type") or "free_text"
        analog_type = row.get("analog_type") or "free_text"
        self.connection.execute(
            """
            INSERT INTO analog_relationship (
                source_system, source_record_id, anchor_type, anchor_label,
                analog_type, analog_label, overall_score, dimension_scores,
                rationale, asserted_at, source_document_id
            )
            VALUES ('convoke', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                source_record_id,
                anchor_type,
                row["anchor_name"],
                analog_type,
                row["analog_name"],
                score,
                Jsonb(row.get("dimensions", {})),
                row.get("rationale") or None,
                parse_date(row.get("as_of_date")),
                source_document_id,
            ),
        )
        return True

    def ingest_web_record(
        self,
        row: dict[str, Any],
        *,
        source_system: str,
        locator: str,
        ingestion_run_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        canonical_url = row.get("url") or row.get("source_url") or row.get("link")
        published_date = parse_date(row.get("published_at") or row.get("date"))
        published_at = as_utc_datetime(published_date)
        digest = content_hash(row)
        existed = self.connection.execute(
            """
            SELECT 1 FROM source_document
            WHERE source_system = %s AND locator = %s AND content_sha256 = %s
            """,
            (source_system, locator, digest),
        ).fetchone()
        self.land_source_document(
            source_system=source_system,
            source_record_type="web_record",
            locator=locator,
            canonical_url=canonical_url,
            published_at=published_at,
            raw_payload=row,
            ingestion_run_id=ingestion_run_id,
            metadata=metadata,
        )
        return not bool(existed)
