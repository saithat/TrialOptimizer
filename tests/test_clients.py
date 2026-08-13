from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from trial_optimizer.clients.clinical_trials_gov import ClinicalTrialsGovClient


def test_ctgov_search_paginates_to_limit() -> None:
    requests: list[str] = []

    def requester(url: str) -> dict[str, object]:
        requests.append(url)
        query = parse_qs(urlparse(url).query)
        if query.get("pageToken") == ["next"]:
            return {"studies": [{"id": 3}]}
        return {"studies": [{"id": 1}, {"id": 2}], "nextPageToken": "next"}

    api = ClinicalTrialsGovClient(requester=requester)
    assert [study["id"] for study in api.search("cancer", limit=3)] == [1, 2, 3]
    assert len(requests) == 2
    assert parse_qs(urlparse(requests[0]).query)["query.term"] == ["cancer"]


def test_ctgov_rejects_malformed_nct_id() -> None:
    api = ClinicalTrialsGovClient(requester=lambda _: {})
    with pytest.raises(ValueError, match="Invalid NCT ID"):
        api.get_study("123")
