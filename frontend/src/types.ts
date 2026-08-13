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

export type TrialOutcome = 'success' | 'endpoint-miss' | 'stopped' | 'unassessed'

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
  actualEnrollment: number | null
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
  reviewMethod?: 'rules' | 'model'
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

export interface ProtocolReviewResult {
  reviewId: number | null
  status: 'rules_only' | 'enhanced' | 'fallback' | 'validation_failed'
  message: string | null
  model: string | null
  summary: string | null
  findings: Finding[]
  evidence: EvidenceRecord[]
  evidenceGaps: string[]
  reviewQuestions: string[]
  databaseStatus: 'ready' | 'unavailable'
  saved: boolean
}

export interface ProtocolReviewHistoryItem {
  id: number
  source_type: 'nct' | 'text' | 'demo'
  nct_id: string | null
  title: string
  status: 'rules_only' | 'enhanced' | 'fallback' | 'validation_failed'
  model: string | null
  deterministic_finding_count: number
  model_finding_count: number
  decision_count: number
  created_at: string
}
