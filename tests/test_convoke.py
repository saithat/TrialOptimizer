from __future__ import annotations

from trial_optimizer.convoke import (
    build_program_comparisons,
    build_related_disease_analogs,
    indications_match,
)


def _program(
    drug: str,
    indication: str,
    *,
    target: str,
    stage: str = "Phase 2",
    status: str = "Active",
    trials: list[dict[str, str]] | None = None,
    trial_count_total: int | None = None,
    truncated: bool = False,
) -> dict[str, object]:
    linked_trials = trials or []
    return {
        "drug_name": drug,
        "indication_name": indication,
        "development_stage": stage,
        "program_status": status,
        "organizations": ["Example Pharma"],
        "targets": [target],
        "modalities": ["Small Molecule"],
        "routes_of_administration": ["Oral"],
        "trials": linked_trials,
        "trial_count_total": (
            len(linked_trials) if trial_count_total is None else trial_count_total
        ),
        "trials_truncated": truncated,
        "observed_at": "2026-08-13T20:00:00Z",
    }


def test_indications_match_handles_tracker_name_variants() -> None:
    assert indications_match("Type 2 Diabetes Mellitus", "Type 2 Diabetes")
    assert indications_match("Obesity", "obesity")
    assert not indications_match("Obesity", "Heart Failure")


def test_related_diseases_are_ranked_and_include_deduplicated_trials() -> None:
    shared_trial = {
        "nct_id": "NCT00000002",
        "trial_name": "Cross-indication study",
        "phase": "Phase 3",
        "study_completion_date": "2028-06-01",
    }
    programs = [
        _program("Drug A", "Disease A", target="T1"),
        _program(
            "Drug A",
            "Disease B",
            target="T1",
            stage="Phase 3",
            trials=[shared_trial],
            trial_count_total=4,
            truncated=True,
        ),
        _program(
            "Drug A",
            "Disease B",
            target="T1",
            status="Probable Inactive",
            trials=[shared_trial],
        ),
        _program(
            "Drug C",
            "Disease C",
            target="T1",
            trials=[
                {
                    "nct_id": "NCT00000003",
                    "trial_name": "Shared-target study",
                    "phase": "Phase 2",
                }
            ],
        ),
        _program("Drug D", "Unrelated Disease", target="T9"),
    ]

    related = build_related_disease_analogs(
        programs,
        drug="Drug A",
        disease="Disease A",
    )

    assert [item["indication"] for item in related] == ["Disease B", "Disease C"]
    same_drug = related[0]
    assert same_drug["relationship_kind"] == "same_drug_cross_indication"
    assert same_drug["relationship_basis"] == [
        "Same drug in another indication",
        "Shared target: T1",
        "Shared modality: Small Molecule",
    ]
    assert same_drug["trial_count_returned"] == 1
    assert same_drug["tracker_reported_trial_count"] == 5
    assert same_drug["trials_truncated"] is True
    assert same_drug["trials"][0]["registry_url"].endswith("/NCT00000002")
    assert "cached response contains only the returned subset" in same_drug["summary"]

    shared_target = related[1]
    assert shared_target["relationship_kind"] == "shared_target_cross_indication"
    assert shared_target["relationship_basis"] == [
        "Shared target: T1",
        "Shared modality: Small Molecule",
    ]


def test_program_comparisons_use_latest_rows_and_interleave_drugs() -> None:
    programs = [
        _program("Drug A", "Disease A", target="T1", stage="Phase 3"),
        _program("Drug A", "Disease B", target="T1", stage="Phase 2"),
        _program("Drug A", "Disease C", target="T2", stage="Phase 1"),
        _program("Drug B", "Disease D", target="T3", stage="Phase 3"),
        _program("Drug B", "Disease E", target="T3", stage="Phase 2"),
        {
            **_program("Drug A", "Disease B", target="T1", stage="Phase 1"),
            "observed_at": "2026-08-12T20:00:00Z",
        },
    ]

    comparisons = build_program_comparisons(programs, limit=3)

    assert [(item["anchor_label"], item["analog_label"]) for item in comparisons] == [
        ("Drug A · Disease A", "Drug A · Disease B"),
        ("Drug B · Disease D", "Drug B · Disease E"),
        ("Drug A · Disease A", "Drug A · Disease C"),
    ]
    assert comparisons[0]["overall_score"] is None
    assert comparisons[0]["comparison_basis"] == ["Same drug", "Shared targets: T1"]
    assert "disease b: phase 2, active" in comparisons[0]["rationale"].casefold()


def test_program_comparisons_require_two_indications_for_the_same_drug() -> None:
    comparisons = build_program_comparisons(
        [
            _program("Drug A", "Disease A", target="T1"),
            _program("Drug B", "Disease B", target="T1"),
        ]
    )

    assert comparisons == []
