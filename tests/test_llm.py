from __future__ import annotations

from typing import Any

import pytest

from trial_optimizer.llm import (
    AlternativeDesign,
    CitationValidationError,
    CitedSynthesisClaim,
    LLMRecommendationOutput,
    build_evidence_context,
    validate_citations,
)


def recommendation_fixture() -> dict[str, Any]:
    return {
        "request": {"drug": "Drug A", "disease": "Disease B", "phase": "Phase 2"},
        "evidence_strength": "moderate",
        "recommendation": {
            "phase": "Phase 2",
            "allocation": "Randomized",
            "intervention_model": "Parallel",
            "masking": "Double",
            "primary_purpose": "Treatment",
            "comparator": "Placebo control",
            "route": "Oral",
            "population": {"disease": "Disease B"},
            "sample_size_benchmark": {"median": 120, "trial_count": 1},
            "primary_endpoint_candidates": [
                {
                    "title": "Change in score",
                    "time_frame": "Week 24",
                    "source_nct_id": "NCT00000001",
                }
            ],
            "risk_flags": [],
        },
        "evidence": {
            "successful": [
                {
                    "nct_id": "NCT00000001",
                    "title": "Successful analog",
                    "outcome": "success",
                    "confidence": 0.9,
                    "rationale": "Primary endpoint met.",
                    "registry_url": "https://clinicaltrials.gov/study/NCT00000001",
                    "publication_url": None,
                    "causal_categories": [],
                }
            ],
            "failed": [],
            "completed": [],
            "active": [
                {
                    "nct_id": "NCT00000002",
                    "title": "Convoke active trial",
                    "source": "Convoke Program Tracker",
                    "registry_url": "https://clinicaltrials.gov/study/NCT00000002",
                }
            ],
            "inactive_programs": [
                {
                    "drug": "Drug A",
                    "indication": "Disease C",
                    "status": "Inactive",
                    "linked_trials": [],
                }
            ],
        },
    }


def output_fixture(citation_id: str) -> LLMRecommendationOutput:
    claim = CitedSynthesisClaim(
        statement="The reviewed analog supports a randomized design.",
        evidence_kind="inference",
        citation_ids=[citation_id],
    )
    return LLMRecommendationOutput(
        executive_summary="The evidence supports a cautious starting point.",
        confidence="moderate",
        design_assessment=[claim],
        failure_readthrough=[],
        alternative_designs=[
            AlternativeDesign(
                title="Conservative alternative",
                change="Retain placebo control.",
                rationale="The cited analog used a comparable endpoint.",
                tradeoff="May increase recruitment burden.",
                citation_ids=[citation_id],
            )
        ],
        evidence_gaps=["No reviewed failures were available."],
        expert_review_questions=["Is the endpoint clinically meaningful?"],
    )


def test_evidence_context_excludes_convoke_by_default() -> None:
    context, citation_index = build_evidence_context(
        recommendation_fixture(), include_convoke_context=False
    )

    assert "reviewed:success:NCT00000001" in citation_index
    assert "active:NCT00000002" not in citation_index
    assert not any(
        item["source"] == "Convoke Program Tracker" for item in context["evidence_records"]
    )


def test_citation_validation_accepts_supplied_identifiers() -> None:
    citation_id = "reviewed:success:NCT00000001"
    validate_citations(output_fixture(citation_id), {citation_id})


def test_citation_validation_rejects_invented_identifiers() -> None:
    with pytest.raises(CitationValidationError):
        validate_citations(
            output_fixture("invented:NCT99999999"),
            {"reviewed:success:NCT00000001"},
        )
