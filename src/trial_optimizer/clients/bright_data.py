from __future__ import annotations

import os
from typing import Self

import httpx


class BrightDataClient:
    base_url = "https://api.brightdata.com/datasets/v3"

    def __init__(self, token: str | None = None, *, timeout: float = 120.0) -> None:
        self.token = token or os.getenv("BRIGHT_DATA_API_TOKEN")
        if not self.token:
            raise ValueError("BRIGHT_DATA_API_TOKEN is required")
        self.client = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.token}"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def progress(self, snapshot_id: str) -> dict[str, object]:
        response = self.client.get(f"{self.base_url}/progress/{snapshot_id}")
        response.raise_for_status()
        return response.json()

    def download_snapshot(self, snapshot_id: str, *, output_format: str = "jsonl") -> bytes:
        if output_format not in {"json", "jsonl", "ndjson", "csv"}:
            raise ValueError("output_format must be json, jsonl, ndjson, or csv")
        response = self.client.get(
            f"{self.base_url}/snapshot/{snapshot_id}",
            params={"format": output_format},
        )
        response.raise_for_status()
        return response.content
