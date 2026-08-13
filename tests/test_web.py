from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from trial_optimizer.web import (
    TrialDesignInput,
    create_app,
    endpoint_concept_key,
    endpoint_focus_terms,
    endpoint_relevance_score,
    recommendation_evidence_strength,
)


class FakeStore:
    def __init__(self) -> None:
        self.review_records: list[dict[str, Any]] = []
        self.review_decisions: list[tuple[int, str]] = []

    def is_ready(self) -> bool:
        return True

    def overview(self) -> dict[str, Any]:
        return {
            "metrics": {
                "total_trials": 1,
                "active_trials": 0,
                "trials_with_results": 1,
                "analog_relationships": 1,
                "unresolved_analogs": 0,
                "convoke_program_snapshots": 2,
                "program_comparisons": 1,
                "source_documents": 2,
                "pending_reviews": 0,
                "result_coverage_percent": 100.0,
            },
            "status_breakdown": [{"status": "COMPLETED", "count": 1}],
            "outcome_breakdown": [{"outcome": "success", "count": 1}],
        }

    def trials(
        self, *, search: str | None, status: str | None, limit: int, offset: int
    ) -> dict[str, Any]:
        return {"items": [], "total": 0, "limit": limit, "offset": offset}

    def trial_detail(self, nct_id: str) -> dict[str, Any] | None:
        if nct_id != "NCT00000001":
            return None
        return {"trial": {"nct_id": nct_id}, "outcomes": [], "references": []}

    def analogs(self, *, limit: int) -> list[dict[str, Any]]:
        return []

    def sources(self) -> list[dict[str, Any]]:
        return []

    def recommend_trial_design(self, request: TrialDesignInput) -> dict[str, Any]:
        return {
            "request": request.model_dump(),
            "evidence_strength": "exploratory",
            "recommendation": {"phase": request.phase or "Phase 2"},
            "evidence": {
                "successful": [],
                "failed": [],
                "active": [],
                "completed": [],
                "inactive_programs": [],
                "related_diseases": [],
            },
        }

    def protocol_review_evidence(self, request: Any, *, limit: int = 24) -> list[dict[str, Any]]:
        return [
            {
                "citation_id": "saved:success:NCT00000002",
                "label": "NCT00000002 — Saved comparison",
                "classification": "reviewed_outcome",
                "nct_id": "NCT00000002",
                "title": "Saved comparison",
                "registry_status": "COMPLETED",
                "why_stopped": None,
                "phases": ["PHASE2"],
                "enrollment_count": 100,
                "has_results": True,
                "source_system": "clinicaltrials.gov",
                "source_url": "https://clinicaltrials.gov/study/NCT00000002",
                "conditions": ["Disease B"],
                "interventions": ["Drug A"],
                "reviewed_outcome": "success",
                "outcome_confidence": 0.9,
                "assessment_rationale": "The primary endpoint was met.",
            }
        ][:limit]

    def record_protocol_review(self, record: dict[str, Any]) -> int:
        self.review_records.append(record)
        return len(self.review_records)

    def protocol_review_history(self, *, limit: int) -> list[dict[str, Any]]:
        return [
            {
                "id": index,
                "source_type": record["request"]["source_type"],
                "nct_id": record["request"].get("nct_id"),
                "title": record["request"]["title"],
                "status": record["llm"]["status"],
                "model": record["llm"].get("model"),
                "deterministic_finding_count": len(record["request"].get("findings", [])),
                "model_finding_count": len((record["llm"].get("output") or {}).get("findings", [])),
                "decision_count": 0,
                "created_at": "2026-08-13T12:00:00Z",
            }
            for index, record in enumerate(reversed(self.review_records), start=1)
        ][:limit]

    def record_protocol_review_decision(self, review_id: int, decision: Any) -> None:
        if review_id < 1 or review_id > len(self.review_records):
            raise LookupError("Protocol review not found")
        self.review_decisions.append((review_id, decision.finding_id))


class FakeProtocolEnhancer:
    model = "test-model"
    prompt_version = "test-recommendation"
    include_convoke_context = False

    def enhance(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        return {"status": "enhanced", "output": {}}

    def review_protocol(
        self, review: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "status": "enhanced",
            "provider": "openai",
            "model": self.model,
            "prompt_version": "protocol-review-test",
            "provider_response_id": "resp_test",
            "included_convoke_context": False,
            "citation_index": {},
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            "output": {
                "summary": "One saved comparison supports a focused review.",
                "findings": [
                    {
                        "section_id": "eligibility",
                        "quote": "ECOG 0 only",
                        "category": "Eligibility",
                        "severity": "High",
                        "title": "Check the ECOG restriction",
                        "explanation": "The saved comparison used a broader population.",
                        "suggestion": "Confirm whether ECOG 1 can be included.",
                        "confidence": "Moderate",
                        "citation_ids": ["saved:success:NCT00000002"],
                    }
                ],
                "evidence_gaps": ["No product-specific safety rationale is saved."],
                "expert_review_questions": ["What safety data support the restriction?"],
            },
        }


class FakeClinicalTrialsClient:
    def get_study(self, nct_id: str) -> dict[str, Any]:
        if nct_id != "NCT00000001":
            raise ValueError(f"Invalid NCT ID: {nct_id}")
        return {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": "Example registry study",
                }
            },
            "hasResults": False,
        }


client = TestClient(create_app(FakeStore(), clinical_trials_client=FakeClinicalTrialsClient()))


def test_dashboard_and_health() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="designForm"' in page.text
    assert client.get("/health").json() == {
        "status": "ok",
        "database": "ready",
        "llm": "disabled",
    }


def test_novel_asset_without_reviewed_outcomes_is_exploratory() -> None:
    assert (
        recommendation_evidence_strength(
            reviewed_successes=0,
            reviewed_failures=0,
            direct_trial_records=0,
            direct_program_records=0,
        )
        == "exploratory"
    )
    assert (
        recommendation_evidence_strength(
            reviewed_successes=0,
            reviewed_failures=0,
            direct_trial_records=3,
            direct_program_records=0,
        )
        == "moderate"
    )


def test_obesity_endpoint_ranking_prefers_weight_over_bone_and_pk_measures() -> None:
    parameters = {
        "phase_matched_nct_ids": {"NCT1", "NCT2", "NCT3"},
        "reviewed_success_nct_ids": set(),
        "focus_terms": endpoint_focus_terms("obesity"),
        "phase": "PHASE2",
    }

    weight = endpoint_relevance_score("Percent weight loss", nct_id="NCT1", **parameters)
    bone = endpoint_relevance_score("Change in total vBMD", nct_id="NCT2", **parameters)
    pharmacokinetic = endpoint_relevance_score(
        "AUC concentration-time curve", nct_id="NCT3", **parameters
    )
    metabolic = endpoint_relevance_score(
        "Sleeping metabolic rate after 10% weight loss", nct_id="NCT3", **parameters
    )

    assert weight > bone
    assert weight > pharmacokinetic
    assert weight > metabolic
    assert endpoint_concept_key("Change from baseline in body weight at 48 weeks", "obesity") == (
        endpoint_concept_key("Change From Baseline in Body Weight at Week 48", "obesity")
    )


def test_dashboard_api_and_not_found() -> None:
    assert client.get("/api/overview").json()["metrics"]["total_trials"] == 1
    assert client.get("/api/trials/NCT00000001").status_code == 200
    assert client.get("/api/trials/NCT99999999").status_code == 404


def test_clinical_trials_proxy_contract() -> None:
    response = client.get("/api/clinicaltrials/NCT00000001")
    assert response.status_code == 200
    assert response.json()["protocolSection"]["identificationModule"]["nctId"] == "NCT00000001"
    assert client.get("/api/clinicaltrials/not-an-nct-id").status_code == 422


def test_recommendation_api_accepts_program_brief() -> None:
    response = client.post(
        "/api/recommendations",
        json={"drug": "Drug A", "disease": "Disease B", "phase": "Phase 2"},
    )

    assert response.status_code == 200
    assert response.json()["recommendation"]["phase"] == "Phase 2"


def test_recommendation_returns_before_separate_ai_summary() -> None:
    progressive_client = TestClient(
        create_app(
            FakeStore(),
            enhancer=FakeProtocolEnhancer(),
            clinical_trials_client=FakeClinicalTrialsClient(),
        )
    )

    response = progressive_client.post(
        "/api/recommendations",
        json={"drug": "Drug A", "disease": "Disease B", "phase": "Phase 2"},
    )

    assert response.status_code == 200
    assert response.json()["llm"]["status"] == "pending"
    job_id = response.json()["llm"]["job_id"]
    completed = progressive_client.get(f"/api/recommendations/ai/{job_id}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "enhanced"
    assert progressive_client.get("/api/recommendations/ai/not-a-job").status_code == 404


def test_recommendation_api_validates_required_fields() -> None:
    assert client.post("/api/recommendations", json={"drug": "A"}).status_code == 422


def test_protocol_review_uses_saved_evidence_and_rules_without_model() -> None:
    store = FakeStore()
    review_client = TestClient(create_app(store, clinical_trials_client=FakeClinicalTrialsClient()))
    response = review_client.post(
        "/api/protocol-reviews",
        json={
            "source_type": "text",
            "title": "Example protocol",
            "sections": [{"id": "eligibility", "label": "Eligibility", "text": "ECOG 0 only"}],
            "findings": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rules_only"
    assert response.json()["saved"] is True
    assert response.json()["evidence"][0]["nctId"] == "NCT00000002"
    assert review_client.get("/api/protocol-reviews").json()[0]["title"] == "Example protocol"


def test_protocol_review_adds_model_findings_and_saves_decisions() -> None:
    store = FakeStore()
    review_client = TestClient(
        create_app(
            store,
            enhancer=FakeProtocolEnhancer(),
            clinical_trials_client=FakeClinicalTrialsClient(),
        )
    )
    response = review_client.post(
        "/api/protocol-reviews",
        json={
            "source_type": "text",
            "title": "Example protocol",
            "sections": [{"id": "eligibility", "label": "Eligibility", "text": "ECOG 0 only"}],
            "findings": [],
        },
    )

    body = response.json()
    assert body["status"] == "enhanced"
    assert body["findings"][0]["phrase"] == "ECOG 0 only"
    assert body["findings"][0]["sourceIds"] == ["saved:success:NCT00000002"]
    decision = review_client.post(
        f"/api/protocol-reviews/{body['reviewId']}/decisions",
        json={"finding_id": body["findings"][0]["id"], "decision": "team_review"},
    )
    assert decision.status_code == 204
    assert store.review_decisions


def test_review_routes_serve_a_built_spa(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (assets / "app.js").write_text("export {}", encoding="utf-8")

    review_client = TestClient(
        create_app(
            FakeStore(),
            clinical_trials_client=FakeClinicalTrialsClient(),
            frontend_dist=tmp_path,
        )
    )

    assert 'id="root"' in review_client.get("/review/").text
    assert 'id="root"' in review_client.get("/review/evidence").text
    assert review_client.get("/review/assets/app.js").status_code == 200
