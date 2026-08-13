from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from trial_optimizer.clients.clinical_trials_gov import ClinicalTrialsGovClient
from trial_optimizer.llm import (
    CitationValidationError,
    RecommendationEnhancer,
    build_enhancer_from_environment,
)
from trial_optimizer.normalization import normalize_name

DEFAULT_DATABASE_URL = "postgresql://trialopt:trialopt@localhost:5432/trialopt"
STATIC_DIR = Path(__file__).with_name("static")
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
load_dotenv()
ACTIVE_STATUSES = (
    "RECRUITING",
    "NOT_YET_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
)


class TrialDesignInput(BaseModel):
    drug: str = Field(min_length=2, max_length=200)
    disease: str = Field(min_length=2, max_length=200)
    phase: str | None = Field(default=None, max_length=30)
    target: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=100)


def _mode(values: Sequence[str | None], fallback: str) -> str:
    present = [value for value in values if value]
    return Counter(present).most_common(1)[0][0] if present else fallback


def _percentile(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def _canonical_phase(value: str | None) -> str | None:
    if not value:
        return None
    compact = value.casefold().strip().replace(" ", "").replace("-", "")
    aliases = {
        "1": "PHASE1",
        "phase1": "PHASE1",
        "1b": "PHASE1",
        "phase1b": "PHASE1",
        "1/2": "PHASE1|PHASE2",
        "phase1/2": "PHASE1|PHASE2",
        "2": "PHASE2",
        "phase2": "PHASE2",
        "2b": "PHASE2",
        "phase2b": "PHASE2",
        "2/3": "PHASE2|PHASE3",
        "phase2/3": "PHASE2|PHASE3",
        "3": "PHASE3",
        "phase3": "PHASE3",
        "4": "PHASE4",
        "phase4": "PHASE4",
    }
    return aliases.get(compact, value.upper().replace(" ", ""))


def humanize_category(value: str | None) -> str:
    if not value:
        return "Unknown"
    return " ".join(part.capitalize() for part in value.replace("-", "_").split("_") if part)


class DashboardStore(Protocol):
    def overview(self) -> dict[str, Any]: ...

    def trials(
        self, *, search: str | None, status: str | None, limit: int, offset: int
    ) -> dict[str, Any]: ...

    def trial_detail(self, nct_id: str) -> dict[str, Any] | None: ...

    def analogs(self, *, limit: int) -> list[dict[str, Any]]: ...

    def sources(self) -> list[dict[str, Any]]: ...

    def recommend_trial_design(self, request: TrialDesignInput) -> dict[str, Any]: ...

    def record_recommendation_run(self, record: dict[str, Any]) -> None: ...

    def is_ready(self) -> bool: ...


class PostgresDashboardStore:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL

    @contextmanager
    def _connection(self):
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute("SET search_path TO trialopt, public")
            yield connection

    def is_ready(self) -> bool:
        try:
            with self._connection() as connection:
                connection.execute("SELECT 1 FROM trialopt.trial LIMIT 1")
            return True
        except psycopg.Error:
            return False

    def overview(self) -> dict[str, Any]:
        with self._connection() as connection:
            metrics = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM trial)::int AS total_trials,
                    (SELECT count(*) FROM trial WHERE overall_status = ANY(%s))::int
                        AS active_trials,
                    (SELECT count(*) FROM trial WHERE has_results)::int AS trials_with_results,
                    (SELECT count(*) FROM analog_relationship)::int AS analog_relationships,
                    (SELECT count(*) FROM analog_relationship
                        WHERE resolution_status != 'resolved')::int AS unresolved_analogs,
                    (SELECT count(*) FROM source_document)::int AS source_documents,
                    (
                        (SELECT count(*) FROM evidence_claim WHERE review_status = 'pending') +
                        (SELECT count(*) FROM outcome_assessment WHERE review_status = 'pending') +
                        (SELECT count(*) FROM entity_resolution_candidate
                            WHERE review_status = 'pending')
                    )::int AS pending_reviews
                """,
                (list(ACTIVE_STATUSES),),
            ).fetchone()
            assert metrics is not None
            status_rows = connection.execute(
                """
                SELECT COALESCE(overall_status, 'UNKNOWN') AS status, count(*)::int AS count
                FROM trial
                GROUP BY COALESCE(overall_status, 'UNKNOWN')
                ORDER BY count DESC, status
                """
            ).fetchall()
            outcome_rows = connection.execute(
                """
                SELECT outcome, count(*)::int AS count
                FROM latest_accepted_outcome
                GROUP BY outcome
                ORDER BY count DESC, outcome
                """
            ).fetchall()

        total = metrics["total_trials"]
        metrics["result_coverage_percent"] = (
            round(100 * metrics["trials_with_results"] / total, 1) if total else 0.0
        )
        return {
            "metrics": metrics,
            "status_breakdown": list(status_rows),
            "outcome_breakdown": list(outcome_rows),
        }

    def trials(
        self, *, search: str | None, status: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        filters: list[str] = []
        parameters: list[Any] = []
        if search:
            filters.append(
                """
                (
                    t.nct_id ILIKE %s OR t.brief_title ILIKE %s OR t.official_title ILIKE %s OR
                    EXISTS (
                        SELECT 1
                        FROM trial_condition tc_search
                        WHERE tc_search.trial_version_id = tv.id
                          AND tc_search.source_name ILIKE %s
                    ) OR
                    EXISTS (
                        SELECT 1
                        FROM trial_sponsor ts_search
                        JOIN organization o_search ON o_search.id = ts_search.organization_id
                        WHERE ts_search.trial_version_id = tv.id
                          AND o_search.preferred_name ILIKE %s
                    )
                )
                """
            )
            term = f"%{search}%"
            parameters.extend([term] * 5)
        if status:
            filters.append("t.overall_status = %s")
            parameters.append(status.upper())
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_query = f"""
            SELECT count(*)::int AS count
            FROM trial t
            LEFT JOIN trial_version tv ON tv.trial_id = t.id AND tv.valid_to IS NULL
            {where_clause}
        """
        list_query = f"""
            SELECT
                t.nct_id,
                t.brief_title,
                t.overall_status,
                t.why_stopped,
                t.phases,
                t.enrollment_count,
                t.primary_completion_date,
                t.last_update_posted,
                t.has_results,
                COALESCE(
                    array_agg(DISTINCT o.preferred_name) FILTER (WHERE o.id IS NOT NULL),
                    '{{}}'
                ) AS sponsors,
                COALESCE(
                    array_agg(DISTINCT tc.source_name) FILTER (WHERE tc.source_name IS NOT NULL),
                    '{{}}'
                ) AS conditions,
                COALESCE(
                    array_agg(DISTINCT ti.source_name) FILTER (WHERE ti.source_name IS NOT NULL),
                    '{{}}'
                ) AS interventions,
                accepted.outcome AS assessed_outcome,
                accepted.confidence AS outcome_confidence
            FROM trial t
            LEFT JOIN trial_version tv ON tv.trial_id = t.id AND tv.valid_to IS NULL
            LEFT JOIN trial_sponsor ts ON ts.trial_version_id = tv.id
            LEFT JOIN organization o ON o.id = ts.organization_id
            LEFT JOIN trial_condition tc ON tc.trial_version_id = tv.id
            LEFT JOIN trial_intervention ti ON ti.trial_version_id = tv.id
            LEFT JOIN LATERAL (
                SELECT oa.outcome, oa.confidence
                FROM outcome_assessment oa
                WHERE oa.trial_id = t.id AND oa.review_status = 'accepted'
                ORDER BY oa.evidence_cutoff_date DESC, oa.created_at DESC
                LIMIT 1
            ) accepted ON true
            {where_clause}
            GROUP BY t.id, accepted.outcome, accepted.confidence
            ORDER BY t.last_update_posted DESC NULLS LAST, t.nct_id
            LIMIT %s OFFSET %s
        """
        with self._connection() as connection:
            total_row = connection.execute(count_query, parameters).fetchone()
            rows = connection.execute(list_query, [*parameters, limit, offset]).fetchall()
        assert total_row is not None
        return {"items": list(rows), "total": total_row["count"], "limit": limit, "offset": offset}

    def trial_detail(self, nct_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            trial = connection.execute(
                """
                SELECT
                    t.*,
                    sd.canonical_url,
                    sd.source_updated_at,
                    tv.observed_at AS registry_observed_at
                FROM trial t
                LEFT JOIN trial_version tv ON tv.trial_id = t.id AND tv.valid_to IS NULL
                LEFT JOIN source_document sd ON sd.id = tv.source_document_id
                WHERE t.nct_id = %s
                """,
                (nct_id.upper(),),
            ).fetchone()
            if trial is None:
                return None

            trial_id = trial["id"]
            version = connection.execute(
                "SELECT id FROM trial_version WHERE trial_id = %s AND valid_to IS NULL",
                (trial_id,),
            ).fetchone()
            version_id = version["id"] if version else None
            sponsors: Sequence[dict[str, Any]] = []
            conditions: Sequence[dict[str, Any]] = []
            interventions: Sequence[dict[str, Any]] = []
            outcomes: Sequence[dict[str, Any]] = []
            references: Sequence[dict[str, Any]] = []
            if version_id:
                sponsors = connection.execute(
                    """
                    SELECT o.preferred_name AS name, ts.sponsor_role AS role
                    FROM trial_sponsor ts
                    JOIN organization o ON o.id = ts.organization_id
                    WHERE ts.trial_version_id = %s
                    ORDER BY ts.sponsor_role, o.preferred_name
                    """,
                    (version_id,),
                ).fetchall()
                conditions = connection.execute(
                    "SELECT source_name AS name FROM trial_condition WHERE trial_version_id = %s",
                    (version_id,),
                ).fetchall()
                interventions = connection.execute(
                    """
                    SELECT intervention_type AS type, source_name AS name, description,
                           arm_labels, other_names
                    FROM trial_intervention
                    WHERE trial_version_id = %s
                    ORDER BY source_name
                    """,
                    (version_id,),
                ).fetchall()
                outcomes = connection.execute(
                    """
                    SELECT outcome_type AS type, title, description, time_frame
                    FROM outcome_measure
                    WHERE trial_version_id = %s
                    ORDER BY CASE outcome_type
                        WHEN 'primary' THEN 1 WHEN 'secondary' THEN 2 ELSE 3 END, ordinal
                    """,
                    (version_id,),
                ).fetchall()
                references = connection.execute(
                    """
                    SELECT reference_type AS type, pmid, doi, citation, url
                    FROM trial_reference
                    WHERE trial_version_id = %s
                    """,
                    (version_id,),
                ).fetchall()
            assessment = connection.execute(
                """
                SELECT outcome, endpoint_result, program_disposition, confidence, rationale,
                       evidence_cutoff_date, review_status
                FROM outcome_assessment
                WHERE trial_id = %s
                ORDER BY evidence_cutoff_date DESC, created_at DESC
                LIMIT 1
                """,
                (trial_id,),
            ).fetchone()

        return {
            "trial": trial,
            "sponsors": list(sponsors),
            "conditions": list(conditions),
            "interventions": list(interventions),
            "outcomes": list(outcomes),
            "references": list(references),
            "assessment": assessment,
        }

    def analogs(self, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    anchor_label,
                    analog_label,
                    overall_score,
                    dimension_scores,
                    rationale,
                    asserted_at,
                    resolution_status,
                    ar.source_system,
                    sd.canonical_url AS source_url
                FROM analog_relationship ar
                JOIN source_document sd ON sd.id = ar.source_document_id
                ORDER BY overall_score DESC NULLS LAST, ar.created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return list(rows)

    def sources(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    sd.source_system,
                    count(DISTINCT sd.id)::int AS documents,
                    count(so.id)::int AS observations,
                    max(so.observed_at) AS last_observed_at
                FROM source_document sd
                LEFT JOIN source_observation so ON so.source_document_id = sd.id
                GROUP BY sd.source_system
                ORDER BY documents DESC, sd.source_system
                """
            ).fetchall()
        return list(rows)

    def recommend_trial_design(self, request: TrialDesignInput) -> dict[str, Any]:
        phase = _canonical_phase(request.phase)
        disease_term = f"%{request.disease}%"
        drug_term = f"%{request.drug}%"
        target_term = f"%{request.target}%" if request.target else None

        with self._connection() as connection:
            analog_rows = connection.execute(
                """
                SELECT anchor_label, analog_label, overall_score
                FROM analog_relationship
                WHERE anchor_label ILIKE %s OR analog_label ILIKE %s
                ORDER BY overall_score DESC NULLS LAST
                LIMIT 30
                """,
                (drug_term, drug_term),
            ).fetchall()
            analog_names = {normalize_name(request.drug)}
            for analog in analog_rows:
                analog_names.add(normalize_name(analog["anchor_label"]))
                analog_names.add(normalize_name(analog["analog_label"]))

            trial_rows = connection.execute(
                """
                SELECT
                    t.id,
                    t.nct_id,
                    t.brief_title,
                    t.overall_status,
                    t.phases,
                    t.allocation,
                    t.intervention_model,
                    t.masking,
                    t.primary_purpose,
                    t.enrollment_count,
                    t.minimum_age,
                    t.maximum_age,
                    t.sex,
                    t.has_results,
                    sd.canonical_url,
                    COALESCE(
                        array_agg(DISTINCT tc.source_name)
                            FILTER (WHERE tc.source_name IS NOT NULL), '{}'
                    ) AS conditions,
                    COALESCE(
                        array_agg(DISTINCT ti.source_name)
                            FILTER (WHERE ti.source_name IS NOT NULL), '{}'
                    ) AS interventions,
                    COALESCE(
                        array_agg(DISTINCT ti.normalized_name)
                            FILTER (WHERE ti.normalized_name IS NOT NULL), '{}'
                    ) AS normalized_interventions,
                    COALESCE(
                        array_agg(DISTINCT ta.arm_type)
                            FILTER (WHERE ta.arm_type IS NOT NULL), '{}'
                    ) AS arm_types,
                    accepted.id AS assessment_id,
                    accepted.outcome AS assessed_outcome,
                    accepted.confidence AS outcome_confidence,
                    accepted.rationale AS outcome_rationale,
                    accepted.evidence_cutoff_date,
                    publication.pmid,
                    publication.doi,
                    publication.citation,
                    COALESCE(causal.categories, '{}') AS causal_categories
                FROM trial t
                JOIN trial_version tv ON tv.trial_id = t.id AND tv.valid_to IS NULL
                JOIN trial_condition tc ON tc.trial_version_id = tv.id
                LEFT JOIN trial_intervention ti ON ti.trial_version_id = tv.id
                LEFT JOIN trial_arm ta ON ta.trial_version_id = tv.id
                LEFT JOIN source_document sd ON sd.id = tv.source_document_id
                LEFT JOIN LATERAL (
                    SELECT oa.id, oa.outcome, oa.confidence, oa.rationale,
                           oa.evidence_cutoff_date
                    FROM outcome_assessment oa
                    WHERE oa.trial_id = t.id AND oa.review_status = 'accepted'
                    ORDER BY oa.evidence_cutoff_date DESC, oa.created_at DESC
                    LIMIT 1
                ) accepted ON true
                LEFT JOIN LATERAL (
                    SELECT tr.pmid, tr.doi, tr.citation
                    FROM trial_reference tr
                    WHERE tr.trial_version_id = tv.id
                      AND (tr.pmid IS NOT NULL OR tr.doi IS NOT NULL)
                    ORDER BY CASE tr.reference_type WHEN 'result' THEN 1 ELSE 2 END
                    LIMIT 1
                ) publication ON true
                LEFT JOIN LATERAL (
                    SELECT array_agg(DISTINCT cf.category) AS categories
                    FROM causal_factor cf
                    WHERE cf.outcome_assessment_id = accepted.id
                ) causal ON true
                WHERE tc.source_name ILIKE %s
                GROUP BY
                    t.id, sd.canonical_url, accepted.id, accepted.outcome,
                    accepted.confidence, accepted.rationale, accepted.evidence_cutoff_date,
                    publication.pmid, publication.doi, publication.citation, causal.categories
                ORDER BY t.last_update_posted DESC NULLS LAST
                LIMIT 250
                """,
                (disease_term,),
            ).fetchall()

            primary_outcome_rows = connection.execute(
                """
                SELECT om.title, om.time_frame, t.nct_id, accepted.outcome
                FROM outcome_measure om
                JOIN trial_version tv ON tv.id = om.trial_version_id AND tv.valid_to IS NULL
                JOIN trial t ON t.id = tv.trial_id
                JOIN trial_condition tc ON tc.trial_version_id = tv.id
                LEFT JOIN LATERAL (
                    SELECT oa.outcome
                    FROM outcome_assessment oa
                    WHERE oa.trial_id = t.id AND oa.review_status = 'accepted'
                    ORDER BY oa.evidence_cutoff_date DESC, oa.created_at DESC
                    LIMIT 1
                ) accepted ON true
                WHERE om.outcome_type = 'primary' AND tc.source_name ILIKE %s
                ORDER BY CASE accepted.outcome
                    WHEN 'success' THEN 1 WHEN 'partial_success' THEN 2 ELSE 3 END,
                    om.ordinal
                LIMIT 100
                """,
                (disease_term,),
            ).fetchall()

            convoke_filters = ["drug_name ILIKE %s", "indication_name ILIKE %s"]
            convoke_parameters: list[Any] = [drug_term, disease_term]
            if target_term:
                convoke_filters.append("targets::text ILIKE %s")
                convoke_parameters.append(target_term)
            convoke_rows = connection.execute(
                f"""
                SELECT DISTINCT ON (lower(drug_name), lower(indication_name))
                    drug_name, indication_name, development_stage, program_status,
                    organizations, targets, modalities, routes_of_administration,
                    trials, trial_count_total, trials_truncated, observed_at
                FROM convoke_program_snapshot
                WHERE ({" OR ".join(convoke_filters)})
                ORDER BY lower(drug_name), lower(indication_name), observed_at DESC
                LIMIT 50
                """,
                convoke_parameters,
            ).fetchall()

        requested_disease = normalize_name(request.disease)

        def program_relevance(program: dict[str, Any]) -> int:
            indication = normalize_name(program["indication_name"])
            drug = normalize_name(program["drug_name"])
            targets = {normalize_name(value) for value in program["targets"]}
            score = 0
            if requested_disease == indication:
                score += 6
            elif requested_disease in indication or indication in requested_disease:
                score += 3
            if normalize_name(request.drug) == drug:
                score += 4
            if request.target and normalize_name(request.target) in targets:
                score += 2
            return score

        convoke_rows = sorted(convoke_rows, key=program_relevance, reverse=True)

        scored_trials: list[tuple[int, dict[str, Any]]] = []
        for row in trial_rows:
            score = 0
            normalized_conditions = {normalize_name(value) for value in row["conditions"]}
            if requested_disease in normalized_conditions:
                score += 4
            else:
                score += 2
            intervention_names = set(row["normalized_interventions"])
            if normalize_name(request.drug) in intervention_names:
                score += 5
            elif intervention_names & analog_names:
                score += 3
            if phase and any(part in row["phases"] for part in phase.split("|")):
                score += 1
            if row["assessed_outcome"]:
                score += 2
            scored_trials.append((score, dict(row)))
        scored_trials.sort(key=lambda item: item[0], reverse=True)
        candidates = [item[1] for item in scored_trials]

        successful = [
            row for row in candidates if row["assessed_outcome"] in {"success", "partial_success"}
        ][:8]
        failed = [row for row in candidates if row["assessed_outcome"] == "failure"][:8]
        benchmark = [
            row
            for row in candidates
            if not phase or any(part in row["phases"] for part in phase.split("|"))
        ] or candidates
        design_basis = successful or benchmark

        allocation = _mode([row["allocation"] for row in design_basis], "RANDOMIZED")
        intervention_model = _mode([row["intervention_model"] for row in design_basis], "PARALLEL")
        masking = _mode([row["masking"] for row in design_basis], "DOUBLE")
        primary_purpose = _mode([row["primary_purpose"] for row in design_basis], "TREATMENT")
        arm_types = [arm for row in design_basis for arm in row["arm_types"]]
        if any("PLACEBO" in arm for arm in arm_types):
            comparator = "Placebo control"
        elif any("ACTIVE" in arm or "COMPARATOR" in arm for arm in arm_types):
            comparator = "Active comparator"
        else:
            comparator = (
                "Concurrent control; choose placebo or standard of care with clinical input"
            )

        enrollments = [
            int(row["enrollment_count"])
            for row in benchmark
            if row["enrollment_count"] is not None and row["enrollment_count"] > 0
        ]
        endpoint_candidates: list[dict[str, str | None]] = []
        seen_endpoints: set[str] = set()
        preferred_nct_ids = {row["nct_id"] for row in successful}
        ordered_outcomes = sorted(
            primary_outcome_rows,
            key=lambda item: (item["nct_id"] not in preferred_nct_ids, item["title"]),
        )
        for outcome in ordered_outcomes:
            normalized_title = normalize_name(outcome["title"])
            if normalized_title in seen_endpoints:
                continue
            seen_endpoints.add(normalized_title)
            endpoint_candidates.append(
                {
                    "title": outcome["title"],
                    "time_frame": outcome["time_frame"],
                    "source_nct_id": outcome["nct_id"],
                }
            )
            if len(endpoint_candidates) == 3:
                break

        def evidence_item(row: dict[str, Any]) -> dict[str, Any]:
            publication_url = None
            if row.get("pmid"):
                publication_url = f"https://pubmed.ncbi.nlm.nih.gov/{row['pmid']}/"
            elif row.get("doi"):
                publication_url = f"https://doi.org/{row['doi']}"
            return {
                "nct_id": row["nct_id"],
                "title": row["brief_title"],
                "outcome": row["assessed_outcome"],
                "confidence": row["outcome_confidence"],
                "rationale": row["outcome_rationale"],
                "registry_url": row["canonical_url"]
                or f"https://clinicaltrials.gov/study/{row['nct_id']}",
                "publication_url": publication_url,
                "publication": row.get("citation"),
                "causal_categories": row["causal_categories"],
            }

        active_trials: list[dict[str, Any]] = []
        inactive_programs: list[dict[str, Any]] = []
        completed_trials: list[dict[str, Any]] = []
        active_seen: set[str] = set()
        completed_seen: set[str] = set()
        today = datetime.now(UTC).date()
        for program in convoke_rows:
            is_active_program = str(program["program_status"] or "").casefold() == "active"
            if not is_active_program:
                inactive_programs.append(
                    {
                        "drug": program["drug_name"],
                        "indication": program["indication_name"],
                        "stage": program["development_stage"],
                        "status": program["program_status"],
                        "organizations": program["organizations"],
                        "targets": program["targets"],
                        "observed_at": program["observed_at"],
                        "linked_trials": [
                            {
                                "nct_id": trial.get("nct_id"),
                                "title": trial.get("trial_name"),
                                "phase": trial.get("phase"),
                                "completion_date": trial.get("study_completion_date"),
                                "registry_url": (
                                    f"https://clinicaltrials.gov/study/{trial.get('nct_id')}"
                                    if trial.get("nct_id")
                                    else None
                                ),
                            }
                            for trial in program["trials"]
                        ],
                        "source": "Convoke Program Tracker",
                    }
                )
                continue
            for trial in program["trials"]:
                nct_id = trial.get("nct_id")
                if not nct_id or nct_id in active_seen:
                    continue
                completion = trial.get("study_completion_date")
                if completion:
                    try:
                        if date.fromisoformat(completion) < today:
                            continue
                    except ValueError:
                        pass
                active_seen.add(nct_id)
                active_trials.append(
                    {
                        "nct_id": nct_id,
                        "title": trial.get("trial_name"),
                        "phase": trial.get("phase"),
                        "drug": program["drug_name"],
                        "indication": program["indication_name"],
                        "completion_date": completion,
                        "registry_url": f"https://clinicaltrials.gov/study/{nct_id}",
                        "source": "Convoke Program Tracker",
                        "observed_at": program["observed_at"],
                    }
                )
        for row in candidates:
            if row["overall_status"] == "COMPLETED" and row["nct_id"] not in completed_seen:
                completed_seen.add(row["nct_id"])
                completed_trials.append(
                    {
                        "nct_id": row["nct_id"],
                        "title": row["brief_title"],
                        "phase": " / ".join(row["phases"]),
                        "drug": ", ".join(row["interventions"][:2]),
                        "indication": ", ".join(row["conditions"][:2]),
                        "registry_status": row["overall_status"],
                        "assessed_outcome": row["assessed_outcome"],
                        "registry_url": row["canonical_url"]
                        or f"https://clinicaltrials.gov/study/{row['nct_id']}",
                        "source": "ClinicalTrials.gov",
                    }
                )
            if row["overall_status"] not in ACTIVE_STATUSES or row["nct_id"] in active_seen:
                continue
            active_seen.add(row["nct_id"])
            active_trials.append(
                {
                    "nct_id": row["nct_id"],
                    "title": row["brief_title"],
                    "phase": " / ".join(row["phases"]),
                    "drug": ", ".join(row["interventions"][:2]),
                    "indication": ", ".join(row["conditions"][:2]),
                    "completion_date": None,
                    "registry_url": row["canonical_url"]
                    or f"https://clinicaltrials.gov/study/{row['nct_id']}",
                    "source": "ClinicalTrials.gov",
                    "observed_at": None,
                }
            )

        causal_categories = Counter(
            category for row in failed for category in row["causal_categories"]
        )
        risk_flags = [
            {
                "category": category,
                "count": count,
                "message": f"{humanize_category(category)} appeared in {count} reviewed failed trial assessment{'s' if count != 1 else ''}.",
            }
            for category, count in causal_categories.most_common(5)
        ]

        if len(successful) >= 3 and failed:
            evidence_strength = "strong"
        elif len(candidates) >= 5 or successful or failed:
            evidence_strength = "moderate"
        else:
            evidence_strength = "exploratory"

        requested_phase = request.phase or "Phase 2"
        rationale = [
            f"Compared with {len(benchmark)} registered trial(s) in {request.disease}.",
            f"The suggested design uses {len(successful)} reviewed successful and {len(failed)} reviewed failed similar trial(s).",
            f"Related records include {len(active_trials)} active trial(s), {len(completed_trials)} completed trial(s), and {len(inactive_programs)} inactive or discontinued program(s).",
        ]
        if not successful or not failed:
            rationale.append(
                "Successful or failed trial data is missing. Treat this design as a suggestion that requires review."
            )

        return {
            "request": request.model_dump(),
            "generated_at": datetime.now(UTC),
            "evidence_strength": evidence_strength,
            "recommendation": {
                "phase": requested_phase,
                "study_type": "Interventional",
                "allocation": humanize_category(allocation),
                "intervention_model": humanize_category(intervention_model),
                "masking": humanize_category(masking),
                "primary_purpose": humanize_category(primary_purpose),
                "comparator": comparator,
                "route": request.route or "Confirm from formulation and exposure strategy",
                "population": {
                    "disease": request.disease,
                    "minimum_age": _mode(
                        [row["minimum_age"] for row in design_basis], "Not inferred"
                    ),
                    "maximum_age": _mode(
                        [row["maximum_age"] for row in design_basis], "Not inferred"
                    ),
                },
                "sample_size_benchmark": {
                    "median": _percentile(enrollments, 0.5),
                    "lower_quartile": _percentile(enrollments, 0.25),
                    "upper_quartile": _percentile(enrollments, 0.75),
                    "trial_count": len(enrollments),
                    "caveat": "This only describes similar trials. Determine final enrollment with a prespecified statistical power calculation.",
                },
                "primary_endpoint_candidates": endpoint_candidates,
                "rationale": rationale,
                "risk_flags": risk_flags,
            },
            "evidence": {
                "successful": [evidence_item(row) for row in successful],
                "failed": [evidence_item(row) for row in failed],
                "active": active_trials[:15],
                "completed": completed_trials[:15],
                "inactive_programs": inactive_programs[:15],
                "unassessed_context": [
                    {
                        "nct_id": row["nct_id"],
                        "title": row["brief_title"],
                        "registry_status": row["overall_status"],
                        "registry_url": row["canonical_url"]
                        or f"https://clinicaltrials.gov/study/{row['nct_id']}",
                    }
                    for row in candidates
                    if not row["assessed_outcome"]
                    and row["overall_status"] != "COMPLETED"
                    and row["overall_status"] not in ACTIVE_STATUSES
                ][:10],
            },
            "limitations": [
                "This compares historical trials. It is not medical, regulatory, or statistical advice.",
                "A program marked active by Convoke may include linked trials that are already complete; displayed active trials are filtered by study completion date when available.",
                "Only reviewed outcome assessments are labeled successful or failed. Registry completion alone is not success.",
            ],
        }

    def record_recommendation_run(self, record: dict[str, Any]) -> None:
        llm = record["llm"]
        usage = llm.get("usage", {})
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO llm_recommendation_run (
                    request_payload, evidence_snapshot, deterministic_recommendation,
                    llm_output, provider, model, prompt_version, status,
                    provider_response_id, included_convoke_context,
                    input_tokens, output_tokens, total_tokens, error_category
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    Jsonb(record["request"]),
                    Jsonb(record["evidence"]),
                    Jsonb(record["deterministic_recommendation"]),
                    (
                        Jsonb(
                            {
                                "structured_output": llm["output"],
                                "citation_index": llm.get("citation_index", {}),
                            }
                        )
                        if llm.get("output") is not None
                        else None
                    ),
                    llm.get("provider", "openai"),
                    llm["model"],
                    llm["prompt_version"],
                    llm["status"],
                    llm.get("provider_response_id"),
                    bool(llm.get("included_convoke_context", False)),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                    llm.get("error_category"),
                ),
            )


def create_app(
    store: DashboardStore | None = None,
    enhancer: RecommendationEnhancer | None = None,
    clinical_trials_client: ClinicalTrialsGovClient | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    data_store = store or PostgresDashboardStore()
    registry_client = clinical_trials_client or ClinicalTrialsGovClient()
    review_dist = frontend_dist or FRONTEND_DIST
    api = FastAPI(title="Trial Optimizer", version="0.1.0")
    api.state.store = data_store
    api.state.enhancer = enhancer
    api.state.clinical_trials_client = registry_client
    api.state.frontend_dist = review_dist
    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if (review_dist / "assets").is_dir():
        api.mount(
            "/review/assets",
            StaticFiles(directory=review_dist / "assets"),
            name="review-assets",
        )

    def run_query(method_name: str, *args: Any, **kwargs: Any) -> Any:
        try:
            method = getattr(data_store, method_name)
            return method(*args, **kwargs)
        except psycopg.Error as error:
            raise HTTPException(
                status_code=503,
                detail="The evidence database is not available. Start PostgreSQL and initialize it.",
            ) from error

    @api.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    def review_file() -> FileResponse:
        index_file = review_dist / "index.html"
        if not index_file.is_file():
            raise HTTPException(
                status_code=503,
                detail="The protocol reviewer has not been built. Run `npm ci` and `npm run build` in frontend/.",
            )
        return FileResponse(index_file)

    @api.get("/review", include_in_schema=False)
    @api.get("/review/", include_in_schema=False)
    def review_index() -> FileResponse:
        return review_file()

    @api.get("/review/{_path:path}", include_in_schema=False)
    def review_spa(_path: str) -> FileResponse:
        return review_file()

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "database": "ready" if data_store.is_ready() else "unavailable",
            "llm": "ready" if enhancer is not None else "disabled",
        }

    @api.get("/api/overview")
    def overview() -> dict[str, Any]:
        return run_query("overview")

    @api.get("/api/trials")
    def trials(
        search: str | None = Query(None, max_length=200),
        status: str | None = Query(None, max_length=50),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        return run_query("trials", search=search, status=status, limit=limit, offset=offset)

    @api.get("/api/trials/{nct_id}")
    def trial_detail(nct_id: str) -> dict[str, Any]:
        result = run_query("trial_detail", nct_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Trial not found")
        return result

    @api.get("/api/clinicaltrials/{nct_id}")
    def clinical_trial(nct_id: str) -> dict[str, Any]:
        try:
            return registry_client.get_study(nct_id)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except HTTPError as error:
            if error.code == 404:
                raise HTTPException(status_code=404, detail="Trial not found") from error
            raise HTTPException(
                status_code=502,
                detail="ClinicalTrials.gov returned an upstream error.",
            ) from error
        except (URLError, TimeoutError) as error:
            raise HTTPException(
                status_code=502,
                detail="ClinicalTrials.gov is temporarily unavailable.",
            ) from error

    @api.get("/api/analogs")
    def analogs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return run_query("analogs", limit=limit)

    @api.get("/api/sources")
    def sources() -> list[dict[str, Any]]:
        return run_query("sources")

    @api.post("/api/recommendations")
    def recommendation(request: TrialDesignInput) -> dict[str, Any]:
        result = run_query("recommend_trial_design", request)
        if enhancer is None:
            result["llm"] = {
                "status": "disabled",
                "message": "AI review is off. The rules-based comparison is still available.",
            }
            return result

        try:
            llm_result = enhancer.enhance(result)
        except (OpenAIError, ValueError) as error:
            status = (
                "validation_failed" if isinstance(error, CitationValidationError) else "fallback"
            )
            llm_result = {
                "status": status,
                "provider": "openai",
                "model": enhancer.model,
                "prompt_version": enhancer.prompt_version,
                "provider_response_id": None,
                "generated_at": datetime.now(UTC),
                "included_convoke_context": enhancer.include_convoke_context,
                "citation_index": {},
                "output": None,
                "usage": {},
                "error_category": type(error).__name__,
                "message": "AI review was unavailable. The rules-based comparison is still available.",
            }
        result["llm"] = llm_result

        record_method = getattr(data_store, "record_recommendation_run", None)
        if record_method is not None:
            try:
                record_method(
                    jsonable_encoder(
                        {
                            "request": result["request"],
                            "evidence": result["evidence"],
                            "deterministic_recommendation": result["recommendation"],
                            "llm": llm_result,
                        }
                    )
                )
                llm_result["audit_status"] = "stored"
            except psycopg.Error:
                llm_result["audit_status"] = "unavailable"
        return result

    return api


app = create_app(enhancer=build_enhancer_from_environment())
