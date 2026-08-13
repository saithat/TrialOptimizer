export type View = 'review' | 'evidence' | 'versions'

export type FindingCategory =
  | 'Recruitment'
  | 'Eligibility'
  | 'Endpoint'
  | 'Safety'
  | 'Operations'

export type Severity = 'High' | 'Moderate' | 'Review'

export interface ProtocolSection {
  id: string
  label: string
  text: string
}

export type TrialOutcome = 'success' | 'endpoint-miss' | 'stopped'

export interface EvidenceSource {
  label: string
  kind: 'Registry' | 'Publication' | 'Regulatory'
  url: string
}

export interface EvidenceRecord {
  id: string
  nctId: string
  title: string
  phase: string
  status: 'COMPLETED' | 'ACTIVE_NOT_RECRUITING' | 'TERMINATED' | 'WITHDRAWN'
  outcome: TrialOutcome
  outcomeLabel: string
  actualEnrollment: number
  reason: string
  result: string
  relevance: string
  relevanceLabel: string
  analogs: string[]
  sources: EvidenceSource[]
}

export type DecisionEvidenceKind =
  | 'Trial result'
  | 'Regulatory guidance'
  | 'Peer-reviewed study'
  | 'Methodology standard'

export type DecisionEvidenceRelevance =
  | 'Direct precedent'
  | 'Design standard'
  | 'Supporting context'

export interface DecisionEvidence {
  id: string
  title: string
  organization: string
  year: number
  kind: DecisionEvidenceKind
  relevance: DecisionEvidenceRelevance
  summary: string
  supports: string
  limitation: string
  url: string
}

export interface Finding {
  id: string
  category: FindingCategory
  severity: Severity
  title: string
  sectionId: string
  phrase: string
  explanation: string
  suggestion: string
  replacement?: string
  confidence: 'High' | 'Moderate' | 'Needs review'
  sourceIds: string[]
  supportIds: string[]
  evidenceLabel: string
}

export interface ScenarioMetric {
  label: string
  before: string
  after: string
  direction: 'up' | 'down' | 'neutral'
}

export interface TrialProfile {
  nctId: string
  title: string
  status: string
  phase: string
  enrollment: number | null
  enrollmentType: string
  studyType: string
  allocation: string
  interventionModel: string
  masking: string
  primaryPurpose: string
  conditions: string[]
  sponsor: string
  interventions: string[]
  armCount: number
  primaryOutcomeCount: number
  secondaryOutcomeCount: number
  locationCount: number
  hasResults: boolean
  lastUpdated: string
  sourceUrl: string
  strengths: string[]
}

export interface NctReview {
  profile: TrialProfile
  sections: ProtocolSection[]
  findings: Finding[]
}
