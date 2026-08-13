import type { Finding, NctReview, ProtocolSection, TrialProfile } from './types'
import { evidenceRecords } from './data'

interface Outcome {
  measure?: string
  description?: string
  timeFrame?: string
}

interface ClinicalTrialsStudy {
  hasResults?: boolean
  protocolSection?: {
    identificationModule?: {
      nctId?: string
      briefTitle?: string
      officialTitle?: string
      organization?: { fullName?: string }
    }
    statusModule?: {
      overallStatus?: string
      lastUpdatePostDateStruct?: { date?: string }
    }
    sponsorCollaboratorsModule?: {
      leadSponsor?: { name?: string }
    }
    designModule?: {
      studyType?: string
      phases?: string[]
      designInfo?: {
        allocation?: string
        interventionModel?: string
        primaryPurpose?: string
        maskingInfo?: { masking?: string }
      }
      enrollmentInfo?: { count?: number; type?: string }
    }
    conditionsModule?: { conditions?: string[] }
    armsInterventionsModule?: {
      armGroups?: Array<{ label?: string; type?: string }>
      interventions?: Array<{ type?: string; name?: string; description?: string }>
    }
    outcomesModule?: {
      primaryOutcomes?: Outcome[]
      secondaryOutcomes?: Outcome[]
    }
    eligibilityModule?: {
      eligibilityCriteria?: string
      sex?: string
      minimumAge?: string
      maximumAge?: string
      healthyVolunteers?: boolean
    }
    contactsLocationsModule?: {
      locations?: Array<{ facility?: string; city?: string; state?: string; country?: string }>
      centralContacts?: Array<{ name?: string }>
    }
  }
}

const displayEnum = (value?: string) => value ? value.replaceAll('_', ' ').toLowerCase() : 'not specified'

const sentenceCase = (value?: string) => {
  const text = displayEnum(value)
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const trimText = (value: string, maxLength: number) => {
  const normalized = value.replace(/\\([\[\]<>^])/g, '$1').replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, maxLength).replace(/\s+\S*$/, '')}…`
}

const listLabel = (items: string[], fallback: string) => items.length ? items.join(', ') : fallback

const criteriaCount = (criteria: string) => criteria
  .split('\n')
  .map((line) => line.trim())
  .filter((line) => /^(?:[-*•]|\d+[.)])\s+/.test(line)).length

function parseNctId(input: string) {
  return input.toUpperCase().match(/NCT\d{8}/)?.[0] ?? null
}

function diseaseAnalogIds(searchText: string) {
  const nsclc = /non[- ]?small[- ]?cell lung|\bnsclc\b/i.test(searchText)
  const met = /\bmet\b|metex14|capmatinib|tepotinib/i.test(searchText)
  const checkpoint = /pembrolizumab|nivolumab|durvalumab|pd-?1|pd-?l1/i.test(searchText)

  if (nsclc && met && checkpoint) return ['geometry-mono1', 'vision-tepotinib', 'capmatinib-pembro', 'keynote-189', 'nivolumab-combinations']
  if (nsclc && met) return ['geometry-mono1', 'vision-tepotinib', 'tepotinib-cns']
  if (nsclc && checkpoint) return ['keynote-024', 'keynote-189', 'checkmate-026', 'mystic']
  return []
}

function buildReview(study: ClinicalTrialsStudy): NctReview {
  const protocol = study.protocolSection
  const identification = protocol?.identificationModule
  const status = protocol?.statusModule
  const sponsorModule = protocol?.sponsorCollaboratorsModule
  const design = protocol?.designModule
  const designInfo = design?.designInfo
  const conditions = protocol?.conditionsModule?.conditions ?? []
  const arms = protocol?.armsInterventionsModule?.armGroups ?? []
  const interventions = protocol?.armsInterventionsModule?.interventions ?? []
  const outcomes = protocol?.outcomesModule
  const primaryOutcomes = outcomes?.primaryOutcomes ?? []
  const secondaryOutcomes = outcomes?.secondaryOutcomes ?? []
  const eligibility = protocol?.eligibilityModule
  const criteria = eligibility?.eligibilityCriteria ?? ''
  const requirementCount = criteriaCount(criteria)
  const locations = protocol?.contactsLocationsModule?.locations ?? []
  const nctId = identification?.nctId

  if (!nctId || !identification?.briefTitle) throw new Error('The public record is missing the fields Trialy needs to review it.')

  const phase = listLabel((design?.phases ?? []).map(sentenceCase), 'Phase not specified')
  const interventionNames = interventions.map((item) => item.name).filter((name): name is string => Boolean(name))
  const sponsor = sponsorModule?.leadSponsor?.name ?? identification.organization?.fullName ?? 'Sponsor not specified'
  const enrollment = design?.enrollmentInfo?.count ?? null
  const enrollmentType = sentenceCase(design?.enrollmentInfo?.type)
  const overallStatus = sentenceCase(status?.overallStatus)
  const ageRange = `${eligibility?.minimumAge ?? 'minimum age not stated'} to ${eligibility?.maximumAge ?? 'no maximum stated'}`
  const primaryOutcomeText = primaryOutcomes.length
    ? primaryOutcomes.map((outcome) => `${outcome.measure ?? 'Unnamed measure'} (${outcome.timeFrame ?? 'time frame not stated'})`).join('; ')
    : 'No primary outcome is listed.'
  const designSummary = `${sentenceCase(designInfo?.allocation)} allocation, ${sentenceCase(designInfo?.interventionModel)} intervention model, ${sentenceCase(designInfo?.maskingInfo?.masking)} masking.`
  const listedRequirements = `The registry lists ${requirementCount} separate eligibility requirements.`
  const ecogZeroOnly = /ECOG(?: performance status| status| PS)?(?: of)?\s*0(?!\s*(?:-|–|or|and|to)\s*1)/i.test(criteria)
  const renalSixtyCutoff = /(?:eGFR|creatinine clearance|CrCl)[^\n.]{0,60}(?:≥|>|at least)\s*60/i.test(criteria)
  const ecogSummary = 'The posted criteria appear limited to ECOG 0.'
  const renalSummary = 'The posted criteria appear to use a renal-function cutoff of 60.'
  const armSummary = `The registry describes ${arms.length} study arms or cohorts.`
  const outcomeSummary = `The registry lists ${primaryOutcomes.length} primary outcomes and ${secondaryOutcomes.length} secondary outcomes.`
  const siteSummary = enrollment !== null
    ? `The record lists ${locations.length} locations for ${enrollment} ${enrollmentType.toLowerCase()} participants.`
    : `The record lists ${locations.length} locations; enrollment is not stated.`
  const missingTimeFrames = primaryOutcomes.filter((outcome) => !outcome.timeFrame).length
  const missingTimeFrameSummary = `${missingTimeFrames} primary outcome${missingTimeFrames === 1 ? '' : 's'} lack a stated time frame.`
  const resultsSummary = study.hasResults ? 'Results are posted in the public record.' : 'No results are posted in the public record.'

  const sections: ProtocolSection[] = [
    {
      id: 'ctg-synopsis',
      label: 'Registry synopsis',
      text: `${identification.briefTitle} is a ${phase} ${displayEnum(design?.studyType)} study sponsored by ${sponsor} in ${listLabel(conditions, 'an unspecified condition')}. The registry status is ${overallStatus}.`,
    },
    {
      id: 'ctg-design',
      label: '1. Study design and interventions',
      text: `${designSummary} ${armSummary} The primary purpose is ${displayEnum(designInfo?.primaryPurpose)}. Listed interventions: ${listLabel(interventionNames, 'none listed')}.`,
    },
    {
      id: 'ctg-population',
      label: '2. Population and eligibility',
      text: `Eligible ages are ${ageRange}; sex eligibility is ${displayEnum(eligibility?.sex)}. ${listedRequirements}${ecogZeroOnly ? ` ${ecogSummary}` : ''}${renalSixtyCutoff ? ` ${renalSummary}` : ''} Registry criteria excerpt: ${trimText(criteria || 'No detailed eligibility criteria are posted.', 1150)}`,
    },
    {
      id: 'ctg-outcomes',
      label: '3. Outcomes',
      text: `${outcomeSummary} Primary outcome detail: ${trimText(primaryOutcomeText, 900)}${missingTimeFrames ? ` ${missingTimeFrameSummary}` : ''}`,
    },
    {
      id: 'ctg-operations',
      label: '4. Enrollment and registry state',
      text: `${siteSummary} ${resultsSummary} The record was last updated ${status?.lastUpdatePostDateStruct?.date ?? 'on an unspecified date'}.`,
    },
  ]

  const searchText = [identification.briefTitle, identification.officialTitle, conditions.join(' '), interventionNames.join(' '), criteria].join(' ')
  const analogIds = diseaseAnalogIds(searchText).filter((analogId) => {
    const analog = evidenceRecords.find((record) => record.id === analogId)
    return analog?.nctId !== nctId
  })
  const findings: Finding[] = []

  if (requirementCount >= 12) {
    findings.push({
      id: `${nctId}-eligibility-density`,
      category: 'Eligibility',
      severity: requirementCount >= 24 ? 'High' : 'Moderate',
      title: 'Eligibility burden needs a criterion-by-criterion check',
      sectionId: 'ctg-population',
      phrase: listedRequirements,
      explanation: `${requirementCount} separately listed requirements can shrink the candidate pool through their combined effect, even when each criterion looks reasonable alone.`,
      suggestion: 'Classify each criterion as safety-critical, endpoint-critical, or inherited convention; model the combined screen-failure effect before protocol lock.',
      confidence: 'Moderate',
      sourceIds: analogIds.slice(0, 3),
      supportIds: ['nsclc-broadened-eligibility', 'protocol-design-performance', 'ctti-recruitment-framework'],
      evidenceLabel: `${analogIds.length ? `${Math.min(3, analogIds.length)} analogs · ` : ''}3 source briefs`,
    })
  }

  if (ecogZeroOnly) {
    findings.push({
      id: `${nctId}-ecog-zero`,
      category: 'Recruitment',
      severity: 'High',
      title: 'ECOG 0-only eligibility may be unnecessarily narrow',
      sectionId: 'ctg-population',
      phrase: ecogSummary,
      explanation: 'The posted criteria appear to restrict performance status to ECOG 0, excluding ambulatory ECOG 1 patients from an already selected population.',
      suggestion: 'Document the combination-specific safety rationale or evaluate ECOG 0–1 with stratification and prespecified safety monitoring.',
      confidence: 'High',
      sourceIds: analogIds.slice(0, 3),
      supportIds: ['fda-performance-status-2026', 'nsclc-broadened-eligibility'],
      evidenceLabel: `${analogIds.length ? `${Math.min(3, analogIds.length)} analogs · ` : ''}2 source briefs`,
    })
  }

  if (renalSixtyCutoff) {
    findings.push({
      id: `${nctId}-renal-threshold`,
      category: 'Eligibility',
      severity: 'Moderate',
      title: 'Renal cutoff should be tied to product evidence',
      sectionId: 'ctg-population',
      phrase: renalSummary,
      explanation: 'The public criteria appear to require renal function at or above 60 without showing the pharmacokinetic or safety basis in the registry record.',
      suggestion: 'Verify the cutoff against renal clearance, exposure, prior toxicity, and dose-adjustment data; evaluate a lower threshold if those data permit.',
      confidence: 'Needs review',
      sourceIds: analogIds.slice(0, 3),
      supportIds: ['fda-organ-dysfunction', 'asco-friends-organ-function', 'nsclc-broadened-eligibility'],
      evidenceLabel: `${analogIds.length ? `${Math.min(3, analogIds.length)} analogs · ` : ''}3 source briefs`,
    })
  }

  if (arms.length >= 5) {
    findings.push({
      id: `${nctId}-cohort-complexity`,
      category: 'Operations',
      severity: arms.length >= 10 ? 'Moderate' : 'Review',
      title: 'Cohort architecture adds execution pressure',
      sectionId: 'ctg-design',
      phrase: armSummary,
      explanation: `${arms.length} arms or cohorts increase assignment, eligibility, monitoring, analysis, and amendment complexity.`,
      suggestion: 'Map the distinct decision made by each cohort and consolidate cohorts that do not change treatment, dose, biomarker, or analysis strategy.',
      confidence: 'Moderate',
      sourceIds: analogIds.slice(0, 3),
      supportIds: ['protocol-design-performance', 'ctti-recruitment-framework'],
      evidenceLabel: `${analogIds.length ? `${Math.min(3, analogIds.length)} analogs · ` : ''}2 source briefs`,
    })
  }

  const pharmacologicInterventions = interventions.filter((intervention) => ['DRUG', 'BIOLOGICAL'].includes(intervention.type ?? ''))
  if (pharmacologicInterventions.length >= 2) {
    findings.push({
      id: `${nctId}-combination-safety`,
      category: 'Safety',
      severity: 'High',
      title: 'Combination safety controls should be explicit',
      sectionId: 'ctg-design',
      phrase: `Listed interventions: ${listLabel(interventionNames, 'none listed')}.`,
      explanation: `The record includes ${pharmacologicInterventions.length} pharmacologic interventions, creating potential for overlapping toxicities and dose-modification complexity.`,
      suggestion: 'Confirm a safety lead-in or prior combination experience, a DLT window, and prespecified interruption, reduction, pause, and stopping rules.',
      confidence: 'Moderate',
      sourceIds: analogIds,
      supportIds: ['fda-ind-safety-elements', 'fda-oncology-dosing-toolkit'],
      evidenceLabel: `${analogIds.length ? `${analogIds.length} analogs · ` : ''}2 source briefs`,
    })
  }

  if (primaryOutcomes.length > 2) {
    findings.push({
      id: `${nctId}-outcome-multiplicity`,
      category: 'Endpoint',
      severity: 'Moderate',
      title: 'Primary-outcome hierarchy needs clarification',
      sectionId: 'ctg-outcomes',
      phrase: outcomeSummary,
      explanation: `${primaryOutcomes.length} primary outcomes can create multiplicity and interpretation risk unless their hierarchy and error control are explicit.`,
      suggestion: 'Specify whether outcomes are co-primary, hierarchical, or independently descriptive and align the analysis and multiplicity strategy.',
      confidence: 'High',
      sourceIds: analogIds,
      supportIds: ['fda-e9r1-estimands', 'national-academies-missing-data'],
      evidenceLabel: `${analogIds.length ? `${analogIds.length} analogs · ` : ''}2 source briefs`,
    })
  }

  if (missingTimeFrames > 0 || primaryOutcomes.length === 0) {
    findings.push({
      id: `${nctId}-outcome-timeframe`,
      category: 'Endpoint',
      severity: 'High',
      title: primaryOutcomes.length ? 'Primary-outcome time frame is incomplete' : 'No primary outcome is posted',
      sectionId: 'ctg-outcomes',
      phrase: primaryOutcomes.length ? missingTimeFrameSummary : outcomeSummary,
      explanation: primaryOutcomes.length
        ? `${missingTimeFrames} primary outcome records do not state when assessment occurs, weakening reproducibility and interpretation.`
        : 'The public record does not expose a primary outcome for evaluating the study objective.',
      suggestion: 'Add a measure, assessment window, analysis population, intercurrent-event strategy, and estimator for every primary outcome.',
      confidence: 'High',
      sourceIds: analogIds,
      supportIds: ['fda-e9r1-estimands', 'national-academies-missing-data'],
      evidenceLabel: `${analogIds.length ? `${analogIds.length} analogs · ` : ''}2 source briefs`,
    })
  }

  const activeStatus = ['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING'].includes(status?.overallStatus ?? '')
  const participantsPerSite = enrollment !== null && locations.length ? enrollment / locations.length : null
  if ((activeStatus && locations.length === 0) || (participantsPerSite !== null && participantsPerSite > 25)) {
    findings.push({
      id: `${nctId}-site-feasibility`,
      category: 'Recruitment',
      severity: activeStatus && locations.length === 0 ? 'High' : 'Moderate',
      title: 'Site-level enrollment feasibility needs validation',
      sectionId: 'ctg-operations',
      phrase: siteSummary,
      explanation: locations.length
        ? `The public plan implies about ${participantsPerSite?.toFixed(1)} participants per listed location, before accounting for screen failures and site activation timing.`
        : 'The study is active but the public record does not list recruiting locations.',
      suggestion: 'Validate the addressable population, screen-failure rate, competing studies, activation timing, and monthly enrollment target at each site.',
      confidence: 'Moderate',
      sourceIds: analogIds,
      supportIds: ['ctti-recruitment-framework', 'protocol-design-performance'],
      evidenceLabel: `${analogIds.length ? `${analogIds.length} analogs · ` : ''}2 source briefs`,
    })
  }

  const strengths: string[] = []
  if (primaryOutcomes.length > 0 && missingTimeFrames === 0) strengths.push('Every posted primary outcome includes a time frame')
  if (study.hasResults) strengths.push('Results are posted in the public registry')
  if (locations.length >= 5) strengths.push(`${locations.length} locations are listed in the public record`)
  if (design?.enrollmentInfo?.type === 'ACTUAL') strengths.push('Enrollment is reported as actual rather than estimated')
  if (!strengths.length) strengths.push('The study identity and core design fields are available for review')

  const profile: TrialProfile = {
    nctId,
    title: identification.briefTitle,
    status: overallStatus,
    phase,
    enrollment,
    enrollmentType,
    studyType: sentenceCase(design?.studyType),
    allocation: sentenceCase(designInfo?.allocation),
    interventionModel: sentenceCase(designInfo?.interventionModel),
    masking: sentenceCase(designInfo?.maskingInfo?.masking),
    primaryPurpose: sentenceCase(designInfo?.primaryPurpose),
    conditions,
    sponsor,
    interventions: interventionNames,
    armCount: arms.length,
    primaryOutcomeCount: primaryOutcomes.length,
    secondaryOutcomeCount: secondaryOutcomes.length,
    locationCount: locations.length,
    hasResults: Boolean(study.hasResults),
    lastUpdated: status?.lastUpdatePostDateStruct?.date ?? 'Not stated',
    sourceUrl: `https://clinicaltrials.gov/study/${nctId}`,
    strengths,
  }

  return { profile, sections, findings }
}

export async function fetchNctReview(input: string): Promise<NctReview> {
  const nctId = parseNctId(input)
  if (!nctId) throw new Error('Enter an NCT number in the format NCT01234567 or paste a ClinicalTrials.gov study URL.')

  const response = await fetch(`/api/clinicaltrials/${nctId}`, { headers: { Accept: 'application/json' } })
  if (response.status === 404) throw new Error(`ClinicalTrials.gov does not have a public record for ${nctId}.`)
  if (!response.ok) throw new Error(`ClinicalTrials.gov returned ${response.status}. Try again in a moment.`)

  return buildReview(await response.json() as ClinicalTrialsStudy)
}
