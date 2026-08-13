from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from typing import Any, Self
from urllib.parse import urlencode
from urllib.request import Request, urlopen

JsonRequester = Callable[[str], dict[str, Any]]


class ClinicalTrialsGovClient:
    base_url = "https://clinicaltrials.gov/api/v2"

    def __init__(self, *, timeout: float = 30.0, requester: JsonRequester | None = None) -> None:
        self.timeout = timeout
        self.requester = requester or self._request_json

    def close(self) -> None:
        return None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "TrialOptimizer/0.1 (clinical research data ingestion)",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def _get(self, path: str, params: dict[str, str | int]) -> dict[str, Any]:
        return self.requester(f"{self.base_url}{path}?{urlencode(params)}")

    def get_study(self, nct_id: str) -> dict[str, Any]:
        nct_id = nct_id.upper()
        if not re.fullmatch(r"NCT\d{8}", nct_id):
            raise ValueError(f"Invalid NCT ID: {nct_id}")
        return self._get(f"/studies/{nct_id}", {"format": "json"})

    def search(self, query: str, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        yielded = 0
        page_token: str | None = None
        while yielded < limit:
            params: dict[str, str | int] = {
                "query.term": query,
                "format": "json",
                "pageSize": min(1000, limit - yielded),
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._get("/studies", params)
            studies = payload.get("studies", [])
            if not studies:
                return
            for study in studies:
                yield study
                yielded += 1
                if yielded >= limit:
                    return
            page_token = payload.get("nextPageToken")
            if not page_token:
                return
