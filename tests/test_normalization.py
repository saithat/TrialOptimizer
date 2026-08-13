from __future__ import annotations

import json
from pathlib import Path

import pytest

from trial_optimizer.importers import (
    load_convoke_programs,
    load_convoke_rows,
    read_structured_records,
)
from trial_optimizer.normalization import content_hash, normalize_name, parse_ctgov_study

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_ctgov_study_preserves_status_without_inventing_outcome() -> None:
    study = json.loads((FIXTURES / "ctgov_study.json").read_text(encoding="utf-8"))
    record = parse_ctgov_study(study)

    assert record.nct_id == "NCT01234567"
    assert record.overall_status == "TERMINATED"
    assert record.why_stopped == "The primary endpoint was not met"
    assert record.phases == ("PHASE2",)
    assert record.enrollment_count == 120
    assert record.allocation == "RANDOMIZED"
    assert record.intervention_model == "PARALLEL"
    assert record.masking == "DOUBLE"
    assert record.primary_purpose == "TREATMENT"
    assert record.minimum_age == "18 Years"
    assert record.maximum_age == "75 Years"
    assert record.healthy_volunteers is False
    assert record.primary_completion_date.isoformat() == "2023-04-01"
    assert record.last_update_posted.isoformat() == "2024-02-05"
    assert record.has_results is True
    assert [sponsor.role for sponsor in record.sponsors] == ["lead", "collaborator"]
    assert record.interventions[0].other_names == ("Examplemab",)
    assert [outcome.outcome_type for outcome in record.outcomes] == ["primary", "secondary"]
    assert record.references[0].pmid == "12345678"
    assert record.sites[0].country == "United States"


def test_hash_is_stable_across_object_key_order() -> None:
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})


def test_name_normalization_is_conservative() -> None:
    assert normalize_name("Example Bio, Inc.") == "example bio inc"
    assert normalize_name("Anti-PD-1") == "anti pd 1"


def test_jsonl_reader(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.jsonl"
    snapshot.write_text('{"url":"https://a.test"}\n{"url":"https://b.test"}\n', encoding="utf-8")
    assert [row["url"] for row in read_structured_records(snapshot)] == [
        "https://a.test",
        "https://b.test",
    ]


def test_convoke_contract(tmp_path: Path) -> None:
    path = tmp_path / "convoke.csv"
    path.write_text(
        "anchor_name,analog_name,overall_score,dimensions_json\n"
        'Drug A,Drug B,0.82,"{""target"":0.9}"\n',
        encoding="utf-8",
    )
    rows = load_convoke_rows(path)
    assert rows[0]["dimensions"] == {"target": 0.9}


def test_convoke_requires_names(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("anchor_name,analog_name\nDrug A,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="requires anchor_name and analog_name"):
        load_convoke_rows(path)


def test_convoke_program_tracker_contract_includes_inactive_programs(tmp_path: Path) -> None:
    path = tmp_path / "programs.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "drug_id": 12,
                        "drug_name": "Drug A",
                        "indication_id": 34,
                        "indication_name": "Disease B",
                        "program_status": "Discontinued",
                        "trials": [{"nct_id": "NCT00000001"}],
                    }
                ],
                "entity_resolution": [{"query": "Drug A", "matched_name": "Drug A"}],
            }
        ),
        encoding="utf-8",
    )

    programs, resolution = load_convoke_programs(path)

    assert programs[0]["program_status"] == "Discontinued"
    assert programs[0]["trials"][0]["nct_id"] == "NCT00000001"
    assert resolution[0]["matched_name"] == "Drug A"
