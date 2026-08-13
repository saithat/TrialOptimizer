from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from trial_optimizer.web import TrialDesignInput, create_app


class FakeStore:
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
            },
        }


client = TestClient(create_app(FakeStore()))


def test_dashboard_and_health() -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="designForm"' in page.text
    assert client.get("/health").json() == {
        "status": "ok",
        "database": "ready",
        "llm": "disabled",
    }


def test_dashboard_api_and_not_found() -> None:
    assert client.get("/api/overview").json()["metrics"]["total_trials"] == 1
    assert client.get("/api/trials/NCT00000001").status_code == 200
    assert client.get("/api/trials/NCT99999999").status_code == 404


def test_recommendation_api_accepts_program_brief() -> None:
    response = client.post(
        "/api/recommendations",
        json={"drug": "Drug A", "disease": "Disease B", "phase": "Phase 2"},
    )

    assert response.status_code == 200
    assert response.json()["recommendation"]["phase"] == "Phase 2"


def test_recommendation_api_validates_required_fields() -> None:
    assert client.post("/api/recommendations", json={"drug": "A"}).status_code == 422
