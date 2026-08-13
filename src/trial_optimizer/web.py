from __future__ import annotations

import os
from collections import Counter
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol
from urllib.error import HTTPError, URLError
from uuid import uuid4

import psycopg
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from trial_optimizer.clients.clinical_trials_gov import ClinicalTrialsGovClient
from trial_optimizer.convoke import (
    build_program_comparisons,
    build_related_disease_analogs,
    indications_match,
)
from trial_optimizer.llm import (
    PROTOCOL_PROMPT_VERSION,
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


class ProtocolSectionInput(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=240)
    text: str = Field(min_length=1, max_length=30_000)


class ProtocolReviewInput(BaseModel):
    source_type: Literal["nct", "text", "demo"]
    nct_id: str | None = Field(default=None, pattern=r"^NCT[0-9]{8}$")
    title: str = Field(min_length=1, max_length=500)
    sections: list[ProtocolSectionInput] = Field(min_length=1, max_length=80)
    findings: list[dict[str, Any]] = Field(default_factory=list, max_length=80)
    profile: dict[str, Any] | None = None


class ProtocolReviewDecisionInput(BaseModel):
    finding_id: str = Field(min_length=1, max_length=200)
    decision: Literal["accepted", "rejected", "team_review"]
    original_text: str | None = Field(default=None, max_length=10_000)
    replacement_text: str | None = Field(default=None, max_length=10_000)


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


def recommendation_evidence_strength(
    *,
    reviewed_successes: int,
    reviewed_failures: int,
    direct_trial_records: int,
    direct_program_records: int,
) -> str:
    if reviewed_successes >= 3 and reviewed_failures:
        return "strong"
    if reviewed_successes or reviewed_failures or direct_trial_records >= 3 or direct_program_records:
        return "moderate"
    return "exploratory"


def endpoint_focus_terms(disease: str) -> tuple[str, ...]:
    normalized = normalize_name(disease)
    if "obesity" in normalized or "overweight" in normalized:
        return ("weight", "weight loss", "bmi", "waist", "wl")
    if "diabetes" in normalized or "glycemic" in normalized:
        return ("hba1c", "glycated hemoglobin", "glucose", "glycemic")
    if any(term in normalized for term in ("cancer", "carcinoma", "tumor", "lymphoma")):
        return ("progression free", "overall survival", "objective response", "recist")
    return ()


def endpoint_concept_key(title: str, disease: str) -> str:
    normalized_title = normalize_name(title)
    normalized_disease = normalize_name(disease)
    if "obesity" in normalized_disease or "overweight" in normalized_disease:
        if any(
            term in normalized_title
            for term in ("lean body mass", "metabolic rate", "energy expenditure")
        ):
            return normalized_title
        if "weight" in normalized_title or normalized_title == "wl":
            if any(
                term in normalized_title
                for term in ("proportion", "participants", "responder", "at least")
            ):
                return "weight response threshold"
            return "body weight change"
        if "body mass index" in normalized_title or "bmi" in normalized_title:
            return "body mass index"
        if "waist" in normalized_title:
            return "waist measure"
    return normalized_title


def endpoint_relevance_score(
    title: str,
    *,
    nct_id: str,
    phase_matched_nct_ids: set[str],
    reviewed_success_nct_ids: set[str],
    focus_terms: Sequence[str],
    phase: str | None,
) -> int:
    normalized_title = normalize_name(title)
    score = 0
    if nct_id in reviewed_success_nct_ids:
        score += 12
    if nct_id in phase_matched_nct_ids:
        score += 6
    if focus_terms and any(term in normalized_title for term in focus_terms):
        score += 8
    elif focus_terms:
        score -= 4
    if phase and any(
        term in normalized_title
        for term in (
            "area under the curve",
            "auc",
            "concentration time",
            "pharmacokinetic",
            "maximum concentration",
            "cmax",
        )
    ):
        score -= 10
    if any(
        term in normalized_title
        for term in (
            "bone mineral",
            "energy expenditure",
            "lean body mass",
            "metabolic rate",
            "vbmd",
        )
    ):
        score -= 10
    return score


_REVIEW_STOPWORDS = {
    "about",
    "after",
    "before",
    "clinical",
    "criteria",
    "during",
    "eligible",
    "enrollment",
    "ecog",
    "endpoint",
    "outcomes",
    "outcome",
    "performance",
    "phase",
    "participants",
    "patients",
    "primary",
    "protocol",
    "response",
    "status",
    "study",
    "treatment",
    "trial",
    "weeks",
    "with",
    "multiple",
}


def _review_terms(request: ProtocolReviewInput) -> list[str]:
    profile = request.profile or {}
    candidates = [
        *profile.get("conditions", []),
        *profile.get("interventions", []),
    ]
    if not candidates:
        combined = " ".join([request.title, *(section.text for section in request.sections)])
        candidates = [
            token
            for token in normalize_name(combined).split()
            if len(token) >= 5 and token not in _REVIEW_STOPWORDS
        ][:20]
    terms: list[str] = []
    for candidate in candidates:
        term = normalize_name(str(candidate)).strip()
        if len(term) >= 3 and term not in terms:
            terms.append(term)
    return terms[:20]


def _frontend_review_evidence(item: dict[str, Any]) -> dict[str, Any]:
    reviewed_outcome = item.get("reviewed_outcome")
    registry_status = str(item.get("registry_status") or "UNKNOWN")
    if reviewed_outcome in {"success", "partial_success"}:
        outcome = "success"
        outcome_label = "Reviewed success" if reviewed_outcome == "success" else "Partial success"
    elif reviewed_outcome == "failure":
        if registry_status in {"TERMINATED", "WITHDRAWN", "SUSPENDED"}:
            outcome = "stopped"
            outcome_label = "Reviewed failure; stopped"
        else:
            outcome = "endpoint-miss"
            outcome_label = "Reviewed failure"
    elif registry_status in {"TERMINATED", "WITHDRAWN", "SUSPENDED"}:
        outcome = "stopped"
        outcome_label = "Stopped; outcome not reviewed"
    else:
        outcome = "unassessed"
        outcome_label = "Outcome not reviewed"

    phases = item.get("phases") or []
    conditions = [str(value) for value in item.get("conditions") or []]
    interventions = [str(value) for value in item.get("interventions") or []]
    rationale = item.get("assessment_rationale")
    why_stopped = item.get("why_stopped")
    return {
        "id": item["citation_id"],
        "citationId": item["citation_id"],
        "nctId": item["nct_id"],
        "title": item.get("title") or "Untitled trial",
        "phase": " / ".join(humanize_category(value) for value in phases) or "Phase not stated",
        "status": registry_status,
        "outcome": outcome,
        "outcomeLabel": outcome_label,
        "actualEnrollment": item.get("enrollment_count"),
        "reason": rationale or why_stopped or "No accepted outcome assessment is saved.",
        "result": (
            rationale
            or (f"Registry reason for stopping: {why_stopped}" if why_stopped else None)
            or "The saved registry record does not establish the trial outcome."
        ),
        "relevance": "Matched using the condition, intervention, or protocol text.",
        "relevanceLabel": "Saved database match",
        "analogs": [*conditions[:3], *interventions[:3]],
        "sources": [
            {
                "label": "ClinicalTrials.gov",
                "kind": "Registry",
                "url": item.get("source_url")
                or f"https://clinicaltrials.gov/study/{item['nct_id']}",
            }
        ],
        "sourceSystem": item.get("source_system"),
    }


def _frontend_model_findings(llm_result: dict[str, Any]) -> list[dict[str, Any]]:
    output = llm_result.get("output") or {}
    findings = []
    for index, finding in enumerate(output.get("findings") or [], start=1):
        citations = finding.get("citation_ids") or []
        findings.append(
            {
                "id": f"model-{index}-{abs(hash(finding.get('title', 'finding')))}",
                "category": finding["category"],
                "severity": finding["severity"],
                "title": finding["title"],
                "sectionId": finding["section_id"],
                "phrase": finding["quote"],
                "explanation": finding["explanation"],
                "suggestion": finding["suggestion"],
                "confidence": finding["confidence"],
                "sourceIds": citations,
                "supportIds": [],
                "evidenceLabel": f"{len(citations)} saved record{'s' if len(citations) != 1 else ''}",
                "reviewMethod": "model",
            }
        )
    return findings


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

    def protocol_review_evidence(
        self, request: ProtocolReviewInput | None, *, limit: int = 24
    ) -> list[dict[str, Any]]: ...

    def record_protocol_review(self, record: dict[str, Any]) -> int: ...

    def protocol_review_history(self, *, limit: int) -> list[dict[str, Any]]: ...

    def record_protocol_review_decision(
        self, review_id: int, decision: ProtocolReviewDecisionInput
    ) -> None: ...

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
                    (SELECT count(*) FROM convoke_program_snapshot)::int
                        AS convoke_program_snapshots,
                    (
                        (SELECT count(*) FROM analog_relationship) +
                        COALESCE((
                            SELECT sum(program_count - 1)
                            FROM (
                                SELECT count(*) AS program_count
                                FROM (
                                    SELECT DISTINCT
                                        lower(drug_name) AS drug_name,
                                        lower(indication_name) AS indication_name
                                    FROM convoke_program_snapshot
                                ) saved_programs
                                GROUP BY drug_name
                                HAVING count(*) > 1
                            ) comparable_drugs
                        ), 0)
                    )::int AS program_comparisons,
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
            explicit_rows = connection.execute(
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
            remaining = limit - len(explicit_rows)
            if remaining <= 0:
                return list(explicit_rows)
            program_rows = connection.execute(
                """
                SELECT DISTINCT ON (lower(cps.drug_name), lower(cps.indication_name))
                    cps.drug_name,
                    cps.indication_name,
                    cps.development_stage,
                    cps.program_status,
                    cps.organizations,
                    cps.targets,
                    cps.modalities,
                    cps.routes_of_administration,
                    cps.trials,
                    cps.trial_count_total,
                    cps.trials_truncated,
                    cps.observed_at,
                    sd.canonical_url AS source_url
                FROM convoke_program_snapshot cps
                JOIN source_document sd ON sd.id = cps.source_document_id
                ORDER BY
                    lower(cps.drug_name),
                    lower(cps.indication_name),
                    cps.observed_at DESC,
                    cps.id DESC
                """
            ).fetchall()

        explicit_pairs = {
            frozenset(
                {
                    normalize_name(str(row["anchor_label"])),
                    normalize_name(str(row["analog_label"])),
                }
            )
            for row in explicit_rows
        }
        derived_rows = [
            row
            for row in build_program_comparisons(
                [dict(program) for program in program_rows], limit=limit
            )
            if frozenset(
                {
                    normalize_name(str(row["anchor_label"])),
                    normalize_name(str(row["analog_label"])),
                }
            )
            not in explicit_pairs
        ]
        return [*explicit_rows, *derived_rows[:remaining]]

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

    def protocol_review_evidence(
        self, request: ProtocolReviewInput | None, *, limit: int = 24
    ) -> list[dict[str, Any]]:
        terms = _review_terms(request) if request is not None else []
        if request is not None and not terms:
            return []
        nct_id = request.nct_id if request is not None else None
        with self._connection() as connection:
            rows = connection.execute(
                """
                WITH review_terms AS (
                    SELECT unnest(%s::text[]) AS term
                )
                SELECT
                    t.nct_id,
                    t.brief_title AS title,
                    t.overall_status AS registry_status,
                    t.why_stopped,
                    t.phases,
                    t.enrollment_count,
                    t.has_results,
                    t.last_update_posted,
                    sd.source_system,
                    COALESCE(
                        sd.canonical_url,
                        'https://clinicaltrials.gov/study/' || t.nct_id
                    ) AS source_url,
                    COALESCE(
                        (
                            SELECT array_agg(DISTINCT tc.source_name ORDER BY tc.source_name)
                            FROM trial_condition tc
                            WHERE tc.trial_version_id = tv.id
                        ),
                        '{}'::text[]
                    ) AS conditions,
                    COALESCE(
                        (
                            SELECT array_agg(DISTINCT ti.source_name ORDER BY ti.source_name)
                            FROM trial_intervention ti
                            WHERE ti.trial_version_id = tv.id
                        ),
                        '{}'::text[]
                    ) AS interventions,
                    accepted.outcome AS reviewed_outcome,
                    accepted.confidence AS outcome_confidence,
                    accepted.rationale AS assessment_rationale
                FROM trial t
                LEFT JOIN trial_version tv ON tv.trial_id = t.id AND tv.valid_to IS NULL
                LEFT JOIN source_document sd ON sd.id = tv.source_document_id
                LEFT JOIN LATERAL (
                    SELECT oa.outcome, oa.confidence, oa.rationale
                    FROM outcome_assessment oa
                    WHERE oa.trial_id = t.id AND oa.review_status = 'accepted'
                    ORDER BY oa.evidence_cutoff_date DESC, oa.created_at DESC
                    LIMIT 1
                ) accepted ON true
                WHERE (%s::text IS NULL OR t.nct_id != %s)
                  AND (
                    NOT EXISTS (SELECT 1 FROM review_terms)
                    OR EXISTS (
                        SELECT 1
                        FROM review_terms rt
                        WHERE lower(COALESCE(t.brief_title, '')) LIKE '%%' || rt.term || '%%'
                           OR EXISTS (
                                SELECT 1 FROM trial_condition tc_match
                                WHERE tc_match.trial_version_id = tv.id
                                  AND lower(tc_match.source_name) LIKE '%%' || rt.term || '%%'
                           )
                           OR EXISTS (
                                SELECT 1 FROM trial_intervention ti_match
                                WHERE ti_match.trial_version_id = tv.id
                                  AND lower(ti_match.source_name) LIKE '%%' || rt.term || '%%'
                           )
                    )
                  )
                ORDER BY
                    (accepted.outcome IS NOT NULL) DESC,
                    accepted.confidence DESC NULLS LAST,
                    t.last_update_posted DESC NULLS LAST,
                    t.nct_id
                LIMIT %s
                """,
                (terms, nct_id, nct_id, limit),
            ).fetchall()

        evidence: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            outcome = item.get("reviewed_outcome")
            item["citation_id"] = (
                f"saved:{outcome}:{item['nct_id']}"
                if outcome
                else f"saved:registry:{item['nct_id']}"
            )
            item["classification"] = (
                "reviewed_outcome" if outcome else "registry_record_outcome_unknown"
            )
            item["label"] = f"{item['nct_id']} — {item.get('title') or 'Untitled trial'}"
            evidence.append(item)
        return evidence

    def record_protocol_review(self, record: dict[str, Any]) -> int:
        llm = record.get("llm") or {}
        usage = llm.get("usage") or {}
        with self._connection() as connection:
            row = connection.execute(
                """
                INSERT INTO protocol_review_run (
                    source_type, nct_id, title, input_snapshot, evidence_snapshot,
                    deterministic_findings, llm_output, provider, model, prompt_version,
                    status, provider_response_id, included_convoke_context,
                    input_tokens, output_tokens, total_tokens, error_category
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                (
                    record["request"]["source_type"],
                    record["request"].get("nct_id"),
                    record["request"]["title"],
                    Jsonb(record["request"]),
                    Jsonb(record.get("evidence", [])),
                    Jsonb(record["request"].get("findings", [])),
                    Jsonb(llm.get("output")) if llm.get("output") is not None else None,
                    llm.get("provider"),
                    llm.get("model"),
                    llm.get("prompt_version"),
                    llm.get("status", "rules_only"),
                    llm.get("provider_response_id"),
                    bool(llm.get("included_convoke_context", False)),
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("total_tokens"),
                    llm.get("error_category"),
                ),
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def protocol_review_history(self, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    pr.id,
                    pr.source_type,
                    pr.nct_id,
                    pr.title,
                    pr.status,
                    pr.model,
                    pr.created_at,
                    jsonb_array_length(pr.deterministic_findings) AS deterministic_finding_count,
                    COALESCE(jsonb_array_length(pr.llm_output -> 'findings'), 0)
                        AS model_finding_count,
                    count(pd.id)::int AS decision_count
                FROM protocol_review_run pr
                LEFT JOIN protocol_review_decision pd ON pd.protocol_review_id = pr.id
                GROUP BY pr.id
                ORDER BY pr.created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return list(rows)

    def record_protocol_review_decision(
        self, review_id: int, decision: ProtocolReviewDecisionInput
    ) -> None:
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM protocol_review_run WHERE id = %s", (review_id,)
            ).fetchone()
            if exists is None:
                raise LookupError("Protocol review not found")
            connection.execute(
                """
                INSERT INTO protocol_review_decision (
                    protocol_review_id, finding_id, decision, original_text, replacement_text
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (protocol_review_id, finding_id)
                DO UPDATE SET
                    decision = EXCLUDED.decision,
                    original_text = EXCLUDED.original_text,
                    replacement_text = EXCLUDED.replacement_text,
                    decided_at = now()
                """,
                (
                    review_id,
                    decision.finding_id,
                    decision.decision,
                    decision.original_text,
                    decision.replacement_text,
                ),
            )

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
                LIMIT 500
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
                LIMIT 250
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
        related_diseases = build_related_disease_analogs(
            [dict(program) for program in convoke_rows],
            drug=request.drug,
            disease=request.disease,
            target=request.target,
        )

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
        requested_drug = normalize_name(request.drug)
        direct_asset_trials = [
            row for row in candidates if requested_drug in set(row["normalized_interventions"])
        ]
        direct_asset_programs = [
            program
            for program in convoke_rows
            if normalize_name(str(program["drug_name"])) == requested_drug
        ]
        target_key = normalize_name(request.target or "")
        target_matched_programs = [
            program
            for program in convoke_rows
            if target_key
            and target_key
            in {normalize_name(str(target)) for target in program["targets"] if target}
        ]
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
        median_enrollment = _percentile(enrollments, 0.5)
        lower_quartile_enrollment = _percentile(enrollments, 0.25)
        upper_quartile_enrollment = _percentile(enrollments, 0.75)
        endpoint_candidates: list[dict[str, str | None]] = []
        seen_endpoints: set[str] = set()
        preferred_nct_ids = {row["nct_id"] for row in successful}
        benchmark_nct_ids = {row["nct_id"] for row in benchmark}
        focus_terms = endpoint_focus_terms(request.disease)
        ordered_outcomes = sorted(
            primary_outcome_rows,
            key=lambda item: (
                -endpoint_relevance_score(
                    item["title"],
                    nct_id=item["nct_id"],
                    phase_matched_nct_ids=benchmark_nct_ids,
                    reviewed_success_nct_ids=preferred_nct_ids,
                    focus_terms=focus_terms,
                    phase=phase,
                ),
                normalize_name(item["title"]),
                item["nct_id"],
            ),
        )
        for outcome in ordered_outcomes:
            concept_key = endpoint_concept_key(outcome["title"], request.disease)
            if concept_key in seen_endpoints:
                continue
            seen_endpoints.add(concept_key)
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
            if not indications_match(program["indication_name"], request.disease):
                continue
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
        review_questions = [
            {
                "category": category,
                "count": count,
                "message": (
                    f"How will the design address {humanize_category(category).casefold()}, which "
                    f"appeared in {count} reviewed failed trial assessment"
                    f"{'s' if count != 1 else ''}?"
                ),
            }
            for category, count in causal_categories.most_common(5)
        ]

        has_direct_asset_history = bool(direct_asset_trials or direct_asset_programs)

        def add_review_question(category: str, message: str) -> None:
            if message not in {item["message"] for item in review_questions}:
                review_questions.append({"category": category, "count": None, "message": message})

        if not has_direct_asset_history:
            add_review_question(
                "asset evidence",
                f"What human safety, exposure, and dose data support moving {request.drug} into "
                f"{request.phase or 'the proposed phase'}?",
            )
        if request.target:
            add_review_question(
                "target",
                f"What evidence shows {request.drug} engages {request.target} at the planned dose, "
                "and how does it differ from existing programs against that target?",
            )
        add_review_question(
            "endpoint",
            f"Which primary endpoint, time frame, and estimand should define benefit in "
            f"{request.disease}?",
        )
        add_review_question(
            "comparator",
            f"Why is {comparator.casefold()} appropriate, and would standard care or an active "
            "comparator answer the development question better?",
        )
        add_review_question(
            "sample size",
            "What effect size, variability, dropout rate, and missing-data assumptions will be "
            "used for the power calculation?",
        )
        add_review_question(
            "population",
            f"Which {request.disease} population should be studied, including prior therapy, "
            "disease severity, major comorbidities, and concomitant treatment?",
        )
        review_questions = review_questions[:8]

        evidence_strength = recommendation_evidence_strength(
            reviewed_successes=len(successful),
            reviewed_failures=len(failed),
            direct_trial_records=len(direct_asset_trials),
            direct_program_records=len(direct_asset_programs),
        )

        requested_phase = request.phase or "Phase 2"
        design_basis_count = len(design_basis)
        allocation_support = sum(row["allocation"] == allocation for row in design_basis)
        model_support = sum(row["intervention_model"] == intervention_model for row in design_basis)
        masking_support = sum(row["masking"] == masking for row in design_basis)
        rationale: list[str] = []
        if has_direct_asset_history:
            rationale.append(
                f"Found {len(direct_asset_trials)} direct trial record(s) and "
                f"{len(direct_asset_programs)} saved program record(s) for {request.drug}."
            )
        else:
            context = f" and programs sharing {request.target}" if request.target else ""
            rationale.append(
                f"No direct trial or program history was found for {request.drug}. The proposed "
                f"structure uses {requested_phase} {request.disease} studies{context}."
            )
        rationale.append(
            f"The structure comes from {design_basis_count} relevant record(s): "
            f"{allocation_support} report {humanize_category(allocation).casefold()} allocation, "
            f"{model_support} report a {humanize_category(intervention_model).casefold()} model, "
            f"and {masking_support} report {humanize_category(masking).casefold()} masking."
        )
        rationale.append(
            f"Enrollment was reported for {len(enrollments)} phase-matched trial(s); the median "
            f"was {median_enrollment if median_enrollment is not None else 'not available'}. This "
            "is context, not a powered sample-size recommendation."
        )
        if request.target:
            target_indications = {
                normalize_name(str(program["indication_name"]))
                for program in target_matched_programs
            }
            rationale.append(
                f"Saved target context includes {len(target_matched_programs)} {request.target} "
                f"program record(s) across {len(target_indications)} indication(s). It does not "
                f"establish efficacy or safety for {request.drug}."
            )
        if not successful and not failed:
            rationale.append(
                "No reviewed success or failure assessments support these choices, so the "
                "recommendation is exploratory."
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
                    "median": median_enrollment,
                    "lower_quartile": lower_quartile_enrollment,
                    "upper_quartile": upper_quartile_enrollment,
                    "trial_count": len(enrollments),
                    "caveat": "This only describes similar trials. Determine final enrollment with a prespecified statistical power calculation.",
                },
                "primary_endpoint_candidates": endpoint_candidates,
                "rationale": rationale,
                "risk_flags": review_questions,
            },
            "evidence": {
                "successful": [evidence_item(row) for row in successful],
                "failed": [evidence_item(row) for row in failed],
                "active": active_trials[:15],
                "completed": completed_trials[:15],
                "inactive_programs": inactive_programs[:15],
                "related_diseases": related_diseases,
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
                "A related disease means another Program Tracker indication connected by the same drug or a shared target. It is not a claim of biological similarity or transferable clinical outcome.",
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
    recommendation_jobs: dict[str, dict[str, Any]] = {}
    recommendation_jobs_lock = Lock()
    api.state.recommendation_jobs = recommendation_jobs
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

    def run_recommendation_ai_job(job_id: str, deterministic_result: dict[str, Any]) -> None:
        assert enhancer is not None
        try:
            llm_result = enhancer.enhance(deterministic_result)
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
                "message": (
                    "AI review was unavailable. The rules-based comparison is still available."
                ),
            }
        except Exception as error:  # noqa: BLE001 - background jobs must become terminal.
            llm_result = {
                "status": "fallback",
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
                "message": (
                    "AI review was unavailable. The rules-based comparison is still available."
                ),
            }

        record_method = getattr(data_store, "record_recommendation_run", None)
        if record_method is not None:
            try:
                record_method(
                    jsonable_encoder(
                        {
                            "request": deterministic_result["request"],
                            "evidence": deterministic_result["evidence"],
                            "deterministic_recommendation": deterministic_result[
                                "recommendation"
                            ],
                            "llm": llm_result,
                        }
                    )
                )
                llm_result["audit_status"] = "stored"
            except psycopg.Error:
                llm_result["audit_status"] = "unavailable"
        llm_result["job_id"] = job_id
        with recommendation_jobs_lock:
            recommendation_jobs[job_id] = jsonable_encoder(llm_result)

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

    @api.get("/api/protocol-reviews")
    def protocol_review_history(
        limit: int = Query(20, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        method = getattr(data_store, "protocol_review_history", None)
        if method is None:
            return []
        try:
            return method(limit=limit)
        except psycopg.Error:
            return []

    @api.post("/api/protocol-reviews")
    def protocol_review(request: ProtocolReviewInput) -> dict[str, Any]:
        request_payload = request.model_dump(mode="json")
        evidence_method = getattr(data_store, "protocol_review_evidence", None)
        evidence: list[dict[str, Any]] = []
        database_status = "unavailable"
        if evidence_method is not None:
            try:
                evidence = evidence_method(request, limit=24)
                database_status = "ready"
            except psycopg.Error:
                evidence = []

        llm_result: dict[str, Any]
        review_method = getattr(enhancer, "review_protocol", None) if enhancer is not None else None
        if review_method is None:
            llm_result = {
                "status": "rules_only",
                "message": "Model review is off. The rule-based findings are still available.",
                "output": None,
            }
        elif not evidence:
            llm_result = {
                "status": "rules_only",
                "provider": "openai",
                "model": enhancer.model,
                "prompt_version": PROTOCOL_PROMPT_VERSION,
                "message": "No matching saved records were available for model review.",
                "output": None,
            }
        else:
            try:
                llm_result = review_method(request_payload, evidence)
            except (OpenAIError, ValueError) as error:
                status = (
                    "validation_failed"
                    if isinstance(error, CitationValidationError)
                    else "fallback"
                )
                llm_result = {
                    "status": status,
                    "provider": "openai",
                    "model": enhancer.model,
                    "prompt_version": PROTOCOL_PROMPT_VERSION,
                    "provider_response_id": None,
                    "generated_at": datetime.now(UTC),
                    "included_convoke_context": enhancer.include_convoke_context,
                    "citation_index": {},
                    "output": None,
                    "usage": {},
                    "error_category": type(error).__name__,
                    "message": "Model review was unavailable. The rule-based findings are still available.",
                }

        review_id: int | None = None
        save_method = getattr(data_store, "record_protocol_review", None)
        if save_method is not None and database_status == "ready":
            try:
                review_id = save_method(
                    jsonable_encoder(
                        {
                            "request": request_payload,
                            "evidence": evidence,
                            "llm": llm_result,
                        }
                    )
                )
            except psycopg.Error:
                review_id = None

        output = llm_result.get("output") or {}
        return {
            "reviewId": review_id,
            "status": llm_result["status"],
            "message": llm_result.get("message"),
            "model": llm_result.get("model"),
            "summary": output.get("summary"),
            "findings": _frontend_model_findings(llm_result),
            "evidence": [_frontend_review_evidence(item) for item in evidence],
            "evidenceGaps": output.get("evidence_gaps", []),
            "reviewQuestions": output.get("expert_review_questions", []),
            "databaseStatus": database_status,
            "saved": review_id is not None,
        }

    @api.post("/api/protocol-reviews/{review_id}/decisions", status_code=204)
    def protocol_review_decision(
        review_id: int,
        decision: ProtocolReviewDecisionInput,
    ) -> None:
        method = getattr(data_store, "record_protocol_review_decision", None)
        if method is None:
            raise HTTPException(status_code=503, detail="Review history is unavailable")
        try:
            method(review_id, decision)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except psycopg.Error as error:
            raise HTTPException(status_code=503, detail="Review history is unavailable") from error

    @api.get("/api/analogs")
    def analogs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
        return run_query("analogs", limit=limit)

    @api.get("/api/sources")
    def sources() -> list[dict[str, Any]]:
        return run_query("sources")

    @api.post("/api/recommendations")
    def recommendation(
        request: TrialDesignInput, background_tasks: BackgroundTasks
    ) -> dict[str, Any]:
        result = run_query("recommend_trial_design", request)
        if enhancer is None:
            result["llm"] = {
                "status": "disabled",
                "message": "AI review is off. The rules-based comparison is still available.",
            }
            return result
        deterministic_result = jsonable_encoder(result)
        job_id = uuid4().hex
        pending = {
            "status": "pending",
            "job_id": job_id,
            "model": enhancer.model,
            "message": (
                "AI summary is running separately. The design and evidence are ready to review."
            ),
        }
        with recommendation_jobs_lock:
            recommendation_jobs[job_id] = pending
        background_tasks.add_task(run_recommendation_ai_job, job_id, deterministic_result)
        result["llm"] = pending
        return result

    @api.get("/api/recommendations/ai/{job_id}")
    def recommendation_ai(job_id: str) -> dict[str, Any]:
        with recommendation_jobs_lock:
            job = recommendation_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="AI summary job not found")
        return job

    return api


app = create_app(enhancer=build_enhancer_from_environment())
