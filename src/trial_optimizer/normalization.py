from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    expanded = {4: f"{value}-01-01", 7: f"{value}-01"}.get(len(value), value)
    try:
        return date.fromisoformat(expanded)
    except ValueError:
        return None


def date_value(value: dict[str, Any] | str | None) -> date | None:
    if isinstance(value, dict):
        return parse_date(value.get("date"))
    return parse_date(value)


def as_utc_datetime(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


@dataclass(frozen=True)
class SponsorRecord:
    name: str
    role: str
    organization_type: str | None = None


@dataclass(frozen=True)
class ArmRecord:
    label: str
    arm_type: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class InterventionRecord:
    name: str
    intervention_type: str | None = None
    description: str | None = None
    arm_labels: tuple[str, ...] = ()
    other_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_type: str
    ordinal: int
    title: str
    description: str | None = None
    time_frame: str | None = None


@dataclass(frozen=True)
class ReferenceRecord:
    reference_type: str
    pmid: str | None = None
    citation: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SiteRecord:
    facility: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    recruitment_status: str | None = None


@dataclass(frozen=True)
class TrialRecord:
    nct_id: str
    brief_title: str | None
    official_title: str | None
    overall_status: str | None
    why_stopped: str | None
    study_type: str | None
    phases: tuple[str, ...]
    allocation: str | None
    intervention_model: str | None
    masking: str | None
    primary_purpose: str | None
    enrollment_count: int | None
    enrollment_type: str | None
    sex: str | None
    minimum_age: str | None
    maximum_age: str | None
    healthy_volunteers: bool | None
    start_date: date | None
    primary_completion_date: date | None
    completion_date: date | None
    last_update_posted: date | None
    has_results: bool
    sponsors: tuple[SponsorRecord, ...] = ()
    conditions: tuple[str, ...] = ()
    arms: tuple[ArmRecord, ...] = ()
    interventions: tuple[InterventionRecord, ...] = ()
    outcomes: tuple[OutcomeRecord, ...] = ()
    references: tuple[ReferenceRecord, ...] = ()
    sites: tuple[SiteRecord, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


def _outcomes(module: dict[str, Any]) -> tuple[OutcomeRecord, ...]:
    records: list[OutcomeRecord] = []
    for key, outcome_type in (
        ("primaryOutcomes", "primary"),
        ("secondaryOutcomes", "secondary"),
        ("otherOutcomes", "other"),
    ):
        for ordinal, outcome in enumerate(module.get(key, []), start=1):
            title = outcome.get("measure")
            if not title:
                continue
            records.append(
                OutcomeRecord(
                    outcome_type=outcome_type,
                    ordinal=ordinal,
                    title=title,
                    description=outcome.get("description"),
                    time_frame=outcome.get("timeFrame"),
                )
            )
    return tuple(records)


def parse_ctgov_study(study: dict[str, Any]) -> TrialRecord:
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    sponsors_module = protocol.get("sponsorCollaboratorsModule", {})
    conditions_module = protocol.get("conditionsModule", {})
    arms_module = protocol.get("armsInterventionsModule", {})
    outcomes_module = protocol.get("outcomesModule", {})
    references_module = protocol.get("referencesModule", {})
    locations_module = protocol.get("contactsLocationsModule", {})
    eligibility_module = protocol.get("eligibilityModule", {})

    nct_id = identification.get("nctId")
    if not nct_id:
        raise ValueError(
            "ClinicalTrials.gov study is missing protocolSection.identificationModule.nctId"
        )

    sponsors: list[SponsorRecord] = []
    lead = sponsors_module.get("leadSponsor")
    if lead and lead.get("name"):
        sponsors.append(SponsorRecord(lead["name"], "lead", lead.get("class")))
    for collaborator in sponsors_module.get("collaborators", []):
        if collaborator.get("name"):
            sponsors.append(
                SponsorRecord(collaborator["name"], "collaborator", collaborator.get("class"))
            )

    arms = tuple(
        ArmRecord(
            label=item["label"],
            arm_type=item.get("type"),
            description=item.get("description"),
        )
        for item in arms_module.get("armGroups", [])
        if item.get("label")
    )

    interventions = tuple(
        InterventionRecord(
            name=item["name"],
            intervention_type=item.get("type"),
            description=item.get("description"),
            arm_labels=tuple(item.get("armGroupLabels", [])),
            other_names=tuple(item.get("otherNames", [])),
        )
        for item in arms_module.get("interventions", [])
        if item.get("name")
    )

    references: list[ReferenceRecord] = []
    for item in references_module.get("references", []):
        references.append(
            ReferenceRecord(
                reference_type=item.get("type", "reference").casefold(),
                pmid=item.get("pmid"),
                citation=item.get("citation"),
            )
        )
    for item in references_module.get("seeAlsoLinks", []):
        references.append(
            ReferenceRecord(
                reference_type="see_also",
                citation=item.get("label"),
                url=item.get("url"),
            )
        )

    sites: list[SiteRecord] = []
    for item in locations_module.get("locations", []):
        geo = item.get("geoPoint", {})
        sites.append(
            SiteRecord(
                facility=item.get("facility"),
                city=item.get("city"),
                state=item.get("state"),
                postal_code=item.get("zip"),
                country=item.get("country"),
                latitude=geo.get("lat"),
                longitude=geo.get("lon"),
                recruitment_status=item.get("status"),
            )
        )

    enrollment = design.get("enrollmentInfo", {})
    design_info = design.get("designInfo", {})
    masking_info = design_info.get("maskingInfo", {})
    return TrialRecord(
        nct_id=nct_id,
        brief_title=identification.get("briefTitle"),
        official_title=identification.get("officialTitle"),
        overall_status=status.get("overallStatus"),
        why_stopped=status.get("whyStopped"),
        study_type=design.get("studyType"),
        phases=tuple(design.get("phases", [])),
        allocation=design_info.get("allocation"),
        intervention_model=design_info.get("interventionModel"),
        masking=masking_info.get("masking"),
        primary_purpose=design_info.get("primaryPurpose"),
        enrollment_count=enrollment.get("count"),
        enrollment_type=enrollment.get("type"),
        sex=eligibility_module.get("sex"),
        minimum_age=eligibility_module.get("minimumAge"),
        maximum_age=eligibility_module.get("maximumAge"),
        healthy_volunteers=eligibility_module.get("healthyVolunteers"),
        start_date=date_value(status.get("startDateStruct")),
        primary_completion_date=date_value(status.get("primaryCompletionDateStruct")),
        completion_date=date_value(status.get("completionDateStruct")),
        last_update_posted=date_value(status.get("lastUpdatePostDateStruct")),
        has_results=bool(study.get("hasResults", False)),
        sponsors=tuple(sponsors),
        conditions=tuple(conditions_module.get("conditions", [])),
        arms=arms,
        interventions=interventions,
        outcomes=_outcomes(outcomes_module),
        references=tuple(references),
        sites=tuple(sites),
        raw=study,
    )
