from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from trial_optimizer.normalization import normalize_name

_STAGE_RANK = {
    "discovery": 0,
    "preclinical": 1,
    "phase 0": 2,
    "phase 1": 3,
    "phase 1/2": 4,
    "phase 2": 5,
    "phase 2/3": 6,
    "phase 3": 7,
    "phase 4": 8,
    "regulatory approval": 9,
    "approval": 9,
    "market": 10,
}


def indications_match(candidate: str, requested: str) -> bool:
    candidate_name = normalize_name(candidate)
    requested_name = normalize_name(requested)
    if candidate_name == requested_name:
        return True
    if min(len(candidate_name), len(requested_name)) < 4:
        return False
    return requested_name in candidate_name or candidate_name in requested_name


def _stage_rank(stage: str | None) -> int:
    return _STAGE_RANK.get(normalize_name(stage or ""), -1)


def _trial_summary(
    *,
    returned_count: int,
    reported_count: int,
    phase_counts: Counter[str],
    program_status_counts: Counter[str],
    truncated: bool,
) -> str:
    phases = ", ".join(
        phase for phase, _ in sorted(phase_counts.items(), key=lambda item: (-item[1], item[0]))
    )
    statuses = ", ".join(
        f"{count} {status.casefold()}"
        for status, count in sorted(program_status_counts.items(), key=lambda item: item[0])
    )
    summary = (
        f"Convoke returned {returned_count} linked trial{'s' if returned_count != 1 else ''}"
        f"{f' across {phases}' if phases else ''} from {statuses or 'programs with unknown status'}."
    )
    if truncated and reported_count > returned_count:
        summary += (
            f" The matched tracker program rows report {reported_count} linked-trial entries in"
            " total; this cached response contains only the returned subset."
        )
    return summary


def build_related_disease_analogs(
    programs: Sequence[dict[str, Any]],
    *,
    drug: str,
    disease: str,
    target: str | None = None,
    limit: int = 10,
    trials_per_disease: int = 8,
) -> list[dict[str, Any]]:
    """Derive cross-indication cohorts from cached Convoke Program Tracker rows.

    These are TrialOptimizer-derived cohorts, not Convoke-authored similarity assertions. A
    candidate must use the requested drug or share a target with the requested program context.
    """

    requested_drug = normalize_name(drug)
    disease_rows = [
        program
        for program in programs
        if indications_match(str(program.get("indication_name") or ""), disease)
    ]
    exact_anchor_rows = [
        program
        for program in disease_rows
        if normalize_name(str(program.get("drug_name") or "")) == requested_drug
    ]
    same_drug_rows = [
        program
        for program in programs
        if normalize_name(str(program.get("drug_name") or "")) == requested_drug
    ]
    anchor_rows = exact_anchor_rows or disease_rows or same_drug_rows
    anchor_target_labels = {
        normalize_name(str(value)): str(value)
        for program in anchor_rows
        for value in program.get("targets") or []
        if value
    }
    if target:
        anchor_target_labels[normalize_name(target)] = target
    anchor_targets = set(anchor_target_labels)
    anchor_modality_labels = {
        normalize_name(str(value)): str(value)
        for program in anchor_rows
        for value in program.get("modalities") or []
        if value
    }
    anchor_modalities = set(anchor_modality_labels)

    grouped: dict[str, dict[str, Any]] = {}
    for program in programs:
        indication = str(program.get("indication_name") or "").strip()
        if not indication or indications_match(indication, disease):
            continue

        program_drug = str(program.get("drug_name") or "").strip()
        same_drug = normalize_name(program_drug) == requested_drug
        program_target_labels = {
            normalize_name(str(value)): str(value)
            for value in program.get("targets") or []
            if value
        }
        shared_target_keys = anchor_targets & set(program_target_labels)
        if not same_drug and not shared_target_keys:
            continue

        key = normalize_name(indication)
        group = grouped.setdefault(
            key,
            {
                "indication": indication,
                "same_drug": False,
                "shared_targets": set(),
                "shared_modalities": set(),
                "programs": [],
                "trials": {},
                "program_status_counts": Counter(),
                "tracker_reported_trial_count": 0,
                "trials_truncated": False,
                "highest_stage": None,
                "highest_stage_rank": -1,
                "latest_observed_at": None,
            },
        )
        group["same_drug"] = group["same_drug"] or same_drug
        group["shared_targets"].update(
            program_target_labels.get(key, anchor_target_labels[key]) for key in shared_target_keys
        )
        program_modality_labels = {
            normalize_name(str(value)): str(value)
            for value in program.get("modalities") or []
            if value
        }
        group["shared_modalities"].update(
            program_modality_labels.get(key, anchor_modality_labels[key])
            for key in anchor_modalities & set(program_modality_labels)
        )

        status = str(program.get("program_status") or "Unknown")
        stage = str(program.get("development_stage") or "Unknown")
        group["program_status_counts"][status] += 1
        if _stage_rank(stage) > group["highest_stage_rank"]:
            group["highest_stage"] = stage
            group["highest_stage_rank"] = _stage_rank(stage)
        observed_at = program.get("observed_at")
        if observed_at is not None and (
            group["latest_observed_at"] is None or observed_at > group["latest_observed_at"]
        ):
            group["latest_observed_at"] = observed_at

        reported_count = program.get("trial_count_total")
        if isinstance(reported_count, int):
            group["tracker_reported_trial_count"] += reported_count
        group["trials_truncated"] = group["trials_truncated"] or bool(
            program.get("trials_truncated")
        )
        group["programs"].append(
            {
                "drug": program_drug,
                "stage": stage,
                "status": status,
                "organizations": list(program.get("organizations") or []),
                "targets": list(program.get("targets") or []),
                "modalities": list(program.get("modalities") or []),
                "routes": list(program.get("routes_of_administration") or []),
            }
        )

        for trial in program.get("trials") or []:
            nct_id = trial.get("nct_id")
            if not nct_id:
                continue
            trial_record = group["trials"].setdefault(
                nct_id,
                {
                    "nct_id": nct_id,
                    "title": trial.get("trial_name"),
                    "phase": trial.get("phase"),
                    "start_date": trial.get("start_date"),
                    "primary_completion_date": trial.get("primary_completion_date"),
                    "study_completion_date": trial.get("study_completion_date"),
                    "registry": trial.get("registry"),
                    "registry_url": f"https://clinicaltrials.gov/study/{nct_id}",
                    "program_drugs": [],
                    "program_statuses": [],
                    "source": "Convoke Program Tracker",
                },
            )
            if program_drug and program_drug not in trial_record["program_drugs"]:
                trial_record["program_drugs"].append(program_drug)
            if status not in trial_record["program_statuses"]:
                trial_record["program_statuses"].append(status)
    related: list[dict[str, Any]] = []
    for group in grouped.values():
        trials = sorted(
            group["trials"].values(),
            key=lambda trial: (
                trial.get("study_completion_date") or "9999-12-31",
                trial["nct_id"],
            ),
        )
        for trial in trials:
            phase = trial.get("phase") or "Phase not reported"
            drugs = ", ".join(trial["program_drugs"]) or "an unnamed program"
            statuses = ", ".join(trial["program_statuses"]) or "status not reported"
            completion = trial.get("study_completion_date")
            trial["summary"] = (
                f"{phase} trial linked by Convoke to {drugs} in {group['indication']}; "
                f"tracker program status: {statuses}."
                f"{f' Listed study completion: {completion}.' if completion else ''}"
            )
        returned_count = len(trials)
        phase_counts = Counter(str(trial.get("phase")) for trial in trials if trial.get("phase"))
        reported_count = max(group["tracker_reported_trial_count"], returned_count)
        relationship_basis = []
        if group["same_drug"]:
            relationship_basis.append("Same drug in another indication")
        if group["shared_targets"]:
            relationship_basis.append(
                f"Shared target: {', '.join(sorted(group['shared_targets']))}"
            )
        if group["shared_modalities"]:
            relationship_basis.append(
                f"Shared modality: {', '.join(sorted(group['shared_modalities']))}"
            )
        related.append(
            {
                "indication": group["indication"],
                "relationship_kind": (
                    "same_drug_cross_indication"
                    if group["same_drug"]
                    else "shared_target_cross_indication"
                ),
                "relationship_basis": relationship_basis,
                "program_count": len(group["programs"]),
                "programs": group["programs"],
                "highest_stage": group["highest_stage"],
                "program_status_counts": dict(group["program_status_counts"]),
                "trial_count_returned": returned_count,
                "tracker_reported_trial_count": reported_count,
                "trials_truncated": group["trials_truncated"],
                "phase_counts": dict(phase_counts),
                "trials": trials[:trials_per_disease],
                "summary": _trial_summary(
                    returned_count=returned_count,
                    reported_count=reported_count,
                    phase_counts=phase_counts,
                    program_status_counts=group["program_status_counts"],
                    truncated=group["trials_truncated"],
                ),
                "observed_at": group["latest_observed_at"],
                "source": "Convoke Program Tracker",
                "derivation": "TrialOptimizer cross-indication cohort",
            }
        )

    related.sort(
        key=lambda item: (
            item["relationship_kind"] != "same_drug_cross_indication",
            -len(item["relationship_basis"]),
            -sum(
                count
                for status, count in item["program_status_counts"].items()
                if status.casefold() == "active"
            ),
            -_stage_rank(item["highest_stage"]),
            -item["trial_count_returned"],
            normalize_name(item["indication"]),
        )
    )
    return related[:limit]


def _program_trial_count(program: dict[str, Any]) -> int:
    reported = program.get("trial_count_total")
    if isinstance(reported, int):
        return reported
    return len(program.get("trials") or [])


def _program_priority(program: dict[str, Any]) -> tuple[int, int, int, str]:
    status = normalize_name(str(program.get("program_status") or ""))
    return (
        -_stage_rank(str(program.get("development_stage") or "")),
        0 if status == "active" else 1,
        -_program_trial_count(program),
        normalize_name(str(program.get("indication_name") or "")),
    )


def _program_summary(program: dict[str, Any]) -> str:
    stage = str(program.get("development_stage") or "stage not reported")
    status = str(program.get("program_status") or "status not reported")
    trial_count = _program_trial_count(program)
    trial_label = "trial" if trial_count == 1 else "trials"
    return f"{stage}, {status.casefold()}, {trial_count} linked {trial_label}"


def build_program_comparisons(
    programs: Sequence[dict[str, Any]], *, limit: int = 20
) -> list[dict[str, Any]]:
    """Build auditable same-drug comparisons from saved Program Tracker rows.

    The latest row for each drug and indication is kept. One reference indication per drug is
    paired with the other indications, and the output is interleaved across drugs so a large
    program does not fill the entire dashboard. No outcome transfer or similarity score is
    inferred.
    """

    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for program in programs:
        drug = str(program.get("drug_name") or "").strip()
        indication = str(program.get("indication_name") or "").strip()
        if not drug or not indication:
            continue
        key = (normalize_name(drug), normalize_name(indication))
        existing = latest.get(key)
        if existing is None or str(program.get("observed_at") or "") >= str(
            existing.get("observed_at") or ""
        ):
            latest[key] = program

    grouped: dict[str, list[dict[str, Any]]] = {}
    for (drug_key, _), program in latest.items():
        grouped.setdefault(drug_key, []).append(program)

    comparison_groups: list[
        tuple[str, dict[str, Any], list[dict[str, Any]]]
    ] = []
    for drug_key, rows in grouped.items():
        if len(rows) < 2:
            continue
        ordered = sorted(rows, key=_program_priority)
        comparison_groups.append((drug_key, ordered[0], ordered[1:]))
    comparison_groups.sort(key=lambda item: (-len(item[2]), item[0]))

    comparisons: list[dict[str, Any]] = []
    maximum_candidates = max((len(item[2]) for item in comparison_groups), default=0)
    for candidate_index in range(maximum_candidates):
        for _, anchor, candidates in comparison_groups:
            if candidate_index >= len(candidates):
                continue
            candidate = candidates[candidate_index]
            drug = str(anchor.get("drug_name") or candidate.get("drug_name") or "Program")
            anchor_indication = str(anchor.get("indication_name") or "Indication not reported")
            candidate_indication = str(
                candidate.get("indication_name") or "Indication not reported"
            )

            anchor_targets = {
                normalize_name(str(target)): str(target)
                for target in anchor.get("targets") or []
                if target
            }
            candidate_targets = {
                normalize_name(str(target)): str(target)
                for target in candidate.get("targets") or []
                if target
            }
            shared_targets = sorted(
                anchor_targets.get(key, candidate_targets[key])
                for key in set(anchor_targets) & set(candidate_targets)
            )
            basis = ["Same drug"]
            if shared_targets:
                basis.append(f"Shared targets: {', '.join(shared_targets)}")

            observed_at = max(
                (anchor.get("observed_at"), candidate.get("observed_at")),
                key=lambda value: str(value or ""),
            )
            comparisons.append(
                {
                    "anchor_label": f"{drug} · {anchor_indication}",
                    "analog_label": f"{drug} · {candidate_indication}",
                    "overall_score": None,
                    "dimension_scores": {},
                    "comparison_basis": basis,
                    "rationale": (
                        f"Both saved records follow {drug} in different indications. "
                        f"{anchor_indication}: {_program_summary(anchor)}. "
                        f"{candidate_indication}: {_program_summary(candidate)}."
                    ),
                    "asserted_at": observed_at,
                    "resolution_status": "saved",
                    "source_system": "Program Tracker",
                    "source_url": candidate.get("source_url") or anchor.get("source_url"),
                    "comparison_type": "same_drug_cross_indication",
                }
            )
            if len(comparisons) >= limit:
                return comparisons
    return comparisons
