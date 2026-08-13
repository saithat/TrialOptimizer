from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def read_structured_records(path: Path) -> Iterator[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                yield dict(row)
        return

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    raise TypeError("JSON array entries must be objects")
                yield row
        elif isinstance(payload, dict):
            yield payload
        else:
            raise ValueError("JSON snapshot must be an object or an array of objects")
        return

    if suffix in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise TypeError(f"Line {line_number} is not a JSON object")
                yield row
        return

    raise ValueError("Snapshot must use .json, .jsonl, .ndjson, or .csv")


def parse_dimensions(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("dimensions_json must contain a JSON object")
    return parsed


def load_convoke_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() != ".csv":
        raise ValueError("The initial Convoke interchange contract is CSV")
    rows = list(read_structured_records(path))
    for index, row in enumerate(rows, start=2):
        if not row.get("anchor_name") or not row.get("analog_name"):
            raise ValueError(f"Convoke CSV row {index} requires anchor_name and analog_name")
        row["dimensions"] = parse_dimensions(row.get("dimensions_json"))
    return rows


def load_convoke_programs(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix.casefold() != ".json":
        raise ValueError("Convoke Program Tracker snapshots must use .json")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("Convoke snapshot must be an object containing an items array")
    items = payload["items"]
    for index, row in enumerate(items, start=1):
        if not isinstance(row, dict):
            raise TypeError(f"Convoke item {index} must be an object")
        if not row.get("drug_name") or not row.get("indication_name"):
            raise ValueError(f"Convoke item {index} requires drug_name and indication_name")
    resolution = payload.get("entity_resolution", [])
    if not isinstance(resolution, list):
        raise TypeError("entity_resolution must be an array")
    return items, resolution
