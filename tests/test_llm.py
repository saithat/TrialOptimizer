from __future__ import annotations

from typing import Any

import pytest

from trial_optimizer.llm import (
    AlternativeDesign,
    CitationValidationError,
    CitedSynthesisClaim,
    ExpertReviewQuestion,
    LLMRecommendationOutput,
    ProtocolReviewFindingOutput,
    ProtocolReviewOutput,
    build_evidence_context,
    build_protocol_evidence_context,
    validate_citations,
    validate_protocol_review,
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
            "related_diseases": [
                {
                    "indication": "Disease C",
                    "relationship_basis": ["Same drug in another indication"],
                    "trials": [
                        {
                            "nct_id": "NCT00000003",
                            "title": "Cross-indication trial",
                            "phase": "Phase 2",
                            "registry_url": "https://clinicaltrials.gov/study/NCT00000003",
                            "program_drugs": ["Drug A"],
                            "program_statuses": ["Active"],
                        }
                    ],
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
        expert_review_questions=[
            ExpertReviewQuestion(
                question="Is the endpoint clinically meaningful?",
                basis="The saved endpoint record does not establish clinical relevance.",
                basis_kind="evidence",
                citation_ids=[citation_id],
            )
        ],
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


def test_evidence_context_includes_related_disease_trials_when_enabled() -> None:
    context, citation_index = build_evidence_context(
        recommendation_fixture(), include_convoke_context=True
    )

    citation_id = "related_indication:1:NCT00000003"
    assert citation_id in citation_index
    record = next(
        item for item in context["evidence_records"] if item["citation_id"] == citation_id
    )
    assert record["classification"] == "related_indication_outcome_unknown"
    assert record["facts"]["related_indication"] == "Disease C"
    assert record["facts"]["outcome_status"] == "unknown"


def test_citation_validation_accepts_supplied_identifiers() -> None:
    citation_id = "reviewed:success:NCT00000001"
    validate_citations(output_fixture(citation_id), {citation_id})


def test_citation_validation_rejects_invented_identifiers() -> None:
    with pytest.raises(CitationValidationError):
        validate_citations(
            output_fixture("invented:NCT99999999"),
            {"reviewed:success:NCT00000001"},
        )


def test_review_question_requires_a_citation_or_evidence_gap() -> None:
    citation_id = "reviewed:success:NCT00000001"
    output = output_fixture(citation_id)
    output.expert_review_questions = [
        ExpertReviewQuestion(
            question="What supports the endpoint?",
            basis="No citation was supplied.",
            basis_kind="evidence",
            citation_ids=[],
        )
    ]
    with pytest.raises(CitationValidationError):
        validate_citations(output, {citation_id})

    output.expert_review_questions[0].basis_kind = "evidence_gap"
    validate_citations(output, {citation_id})


def protocol_output_fixture(*, quote: str, citation_id: str) -> ProtocolReviewOutput:
    return ProtocolReviewOutput(
        summary="Review the eligibility rationale.",
        findings=[
            ProtocolReviewFindingOutput(
                section_id="eligibility",
                quote=quote,
                category="Eligibility",
                severity="High",
                title="Check the ECOG restriction",
                explanation="A saved comparison used broader eligibility.",
                suggestion="Confirm whether ECOG 1 can be included.",
                confidence="Moderate",
                citation_ids=[citation_id],
            )
        ],
        evidence_gaps=[],
        expert_review_questions=[],
    )


def test_protocol_context_uses_saved_records_and_excludes_convoke() -> None:
    review = {
        "source_type": "text",
        "title": "Example",
        "sections": [{"id": "eligibility", "text": "ECOG 0 only"}],
        "findings": [],
    }
    evidence = [
        {
            "citation_id": "saved:success:NCT00000001",
            "nct_id": "NCT00000001",
            "title": "Saved public record",
            "source_system": "clinicaltrials.gov",
        },
        {
            "citation_id": "saved:registry:NCT00000002",
            "nct_id": "NCT00000002",
            "title": "Restricted record",
            "source_system": "convoke",
        },
    ]

    context, citation_index = build_protocol_evidence_context(
        review, evidence, include_convoke_context=False
    )

    assert list(citation_index) == ["saved:success:NCT00000001"]
    assert len(context["saved_evidence"]) == 1


def test_protocol_validation_requires_exact_quote_and_citation() -> None:
    sections = [{"id": "eligibility", "text": "Participants must have ECOG 0 only."}]
    citation_id = "saved:success:NCT00000001"
    validate_protocol_review(
        protocol_output_fixture(quote="ECOG 0 only", citation_id=citation_id),
        sections=sections,
        valid_ids={citation_id},
    )

    plain_nct_output = protocol_output_fixture(quote="ECOG 0 only", citation_id="NCT00000001")
    validate_protocol_review(
        plain_nct_output,
        sections=sections,
        valid_ids={citation_id},
    )
    assert plain_nct_output.findings[0].citation_ids == [citation_id]

    with pytest.raises(CitationValidationError):
        validate_protocol_review(
            protocol_output_fixture(quote="ECOG 0 or 1", citation_id=citation_id),
            sections=sections,
            valid_ids={citation_id},
        )
