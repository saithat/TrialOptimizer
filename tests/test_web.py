from __future__ import annotations

from pathlib import Path
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


client = TestClient(
    create_app(FakeStore(), clinical_trials_client=FakeClinicalTrialsClient())
)


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


def test_recommendation_api_validates_required_fields() -> None:
    assert client.post("/api/recommendations", json={"drug": "A"}).status_code == 422


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
