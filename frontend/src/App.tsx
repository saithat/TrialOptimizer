import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  FileClock,
  FilePlus2,
  FileText,
  FlaskConical,
  Info,
  Layers3,
  Link as LinkIcon,
  ListFilter,
  RotateCcw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'
import { fetchNctReview } from './clinicalTrials'
import { decisionEvidenceRecords, demoFindings, demoProtocol, evidenceRecords, scenarioMetrics } from './data'
import type { DecisionEvidence, EvidenceRecord, Finding, ProtocolSection, Severity, TrialOutcome, TrialProfile, View } from './types'

const severityRank: Record<Severity, number> = {
  High: 0,
  Moderate: 1,
  Review: 2,
}

const cloneSections = () => demoProtocol.map((section) => ({ ...section }))

function App() {
  const [view, setView] = useState<View>('review')
  const [sections, setSections] = useState<ProtocolSection[]>(cloneSections)
  const [findings, setFindings] = useState<Finding[]>(demoFindings)
  const [activeFindingId, setActiveFindingId] = useState(demoFindings[0].id)
  const [acceptedIds, setAcceptedIds] = useState<string[]>([])
  const [filter, setFilter] = useState<'All' | 'High'>('All')
  const [reviewing, setReviewing] = useState(false)
  const [newProtocolOpen, setNewProtocolOpen] = useState(false)
  const [nctLookupOpen, setNctLookupOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [nctInput, setNctInput] = useState('')
  const [nctLoading, setNctLoading] = useState(false)
  const [nctError, setNctError] = useState<string | null>(null)
  const [trialProfile, setTrialProfile] = useState<TrialProfile | null>(null)
  const [isDemo, setIsDemo] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const [evidenceQuery, setEvidenceQuery] = useState('')
  const [evidenceOutcome, setEvidenceOutcome] = useState<'all' | TrialOutcome>('all')
  const toastTimer = useRef<number | null>(null)

  const unresolved = findings.filter((finding) => !acceptedIds.includes(finding.id))
  const visibleFindings = unresolved
    .filter((finding) => filter === 'All' || finding.severity === 'High')
    .sort((a, b) => severityRank[a.severity] - severityRank[b.severity])
  const activeFinding = findings.find((finding) => finding.id === activeFindingId) ?? visibleFindings[0]
  const activeEvidence = evidenceRecords.filter((record) => activeFinding?.sourceIds.includes(record.id))
  const activeDecisionEvidence = decisionEvidenceRecords.filter((record) => activeFinding?.supportIds.includes(record.id))
  const filteredEvidence = evidenceRecords.filter((record) => {
    const query = evidenceQuery.toLowerCase().trim()
    const matchesQuery = !query || `${record.nctId} ${record.title} ${record.reason} ${record.result} ${record.outcomeLabel} ${record.analogs.join(' ')}`.toLowerCase().includes(query)
    const matchesOutcome = evidenceOutcome === 'all' || record.outcome === evidenceOutcome
    return matchesQuery && matchesOutcome
  })

  const directSources = useMemo(
    () => new Set(findings.flatMap((finding) => finding.sourceIds)).size,
    [findings],
  )
  const supportSources = useMemo(
    () => new Set(findings.flatMap((finding) => finding.supportIds)).size,
    [findings],
  )

  useEffect(() => {
    return () => {
      if (toastTimer.current) window.clearTimeout(toastTimer.current)
    }
  }, [])

  function showToast(message: string) {
    setToast(message)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 2600)
  }

  function selectFinding(id: string) {
    setActiveFindingId(id)
    const sectionId = findings.find((finding) => finding.id === id)?.sectionId
    if (sectionId) {
      window.setTimeout(() => {
        document.getElementById(`section-${sectionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }, 30)
    }
  }

  function acceptFinding(finding: Finding) {
    if (finding.replacement) {
      setSections((current) =>
        current.map((section) =>
          section.id === finding.sectionId
            ? { ...section, text: section.text.replace(finding.phrase, finding.replacement!) }
            : section,
        ),
      )
    }
    setAcceptedIds((current) => [...current, finding.id])
    const next = unresolved.find((item) => item.id !== finding.id)
    if (next) setActiveFindingId(next.id)
    showToast(finding.replacement ? 'Suggestion applied to the working draft' : 'Signal marked for team review')
  }

  function resetDemo() {
    setSections(cloneSections())
    setFindings(demoFindings)
    setAcceptedIds([])
    setActiveFindingId(demoFindings[0].id)
    setIsDemo(true)
    setTrialProfile(null)
    setView('review')
    showToast('Demo protocol restored')
  }

  async function reviewNct(event: React.FormEvent) {
    event.preventDefault()
    if (nctLoading) return

    setNctLoading(true)
    setNctError(null)
    try {
      const review = await fetchNctReview(nctInput)
      setSections(review.sections)
      setFindings(review.findings)
      setAcceptedIds([])
      setActiveFindingId(review.findings[0]?.id ?? '')
      setTrialProfile(review.profile)
      setIsDemo(false)
      setFilter('All')
      setView('review')
      setNctInput(review.profile.nctId)
      setNctLookupOpen(false)
      setReviewing(true)
      window.setTimeout(() => {
        setReviewing(false)
        showToast(`${review.profile.nctId} reviewed · ${review.findings.length} signal${review.findings.length === 1 ? '' : 's'}`)
      }, 720)
    } catch (error) {
      setNctError(error instanceof Error ? error.message : 'The study could not be reviewed.')
    } finally {
      setNctLoading(false)
    }
  }

  function importProtocol() {
    const trimmed = importText.trim()
    if (!trimmed) return

    const importedSections = trimmed
      .split(/\n\s*\n/)
      .map((text, index) => ({ id: `imported-${index}`, label: index === 0 ? 'Imported protocol' : `Section ${index + 1}`, text: text.trim() }))

    const importedText = importedSections.map((section) => section.text).join(' ')
    const generated: Finding[] = []

    if (/ECOG(?: performance status)?(?: of)? 0(?!\s*(?:-|–|or)\s*1)/i.test(importedText)) {
      const section = importedSections.find((item) => /ECOG/i.test(item.text))!
      generated.push({
        ...demoFindings[0],
        id: 'import-ecog',
        sectionId: section.id,
        phrase: section.text.match(/ECOG(?: performance status)?(?: of)? 0/i)?.[0] ?? 'ECOG 0',
        replacement: 'ECOG performance status of 0 or 1',
      })
    }
    if (/weekly|every 7 days/i.test(importedText)) {
      const section = importedSections.find((item) => /weekly|every 7 days/i.test(item.text))!
      const phrase = section.text.match(/[^.]*?(?:weekly|every 7 days)[^.]*\./i)?.[0]?.trim() ?? 'weekly'
      generated.push({ ...demoFindings[3], id: 'import-visits', sectionId: section.id, phrase })
    }
    if (/pembrolizumab/i.test(importedText) && /MET/i.test(importedText)) {
      const section = importedSections.find((item) => /pembrolizumab/i.test(item.text))!
      const phrase = section.text.match(/[^.]*pembrolizumab[^.]*\./i)?.[0]?.trim() ?? 'pembrolizumab'
      generated.push({ ...demoFindings[1], id: 'import-combination', sectionId: section.id, phrase })
    }
    if (/without (?:an?|the) (?:tumou?r )?assessment|missing assessment/i.test(importedText)) {
      const section = importedSections.find((item) => /without (?:an?|the) (?:tumou?r )?assessment|missing assessment/i.test(item.text))!
      const phrase = section.text.match(/[^.]*(?:without (?:an?|the) (?:tumou?r )?assessment|missing assessment)[^.]*\./i)?.[0]?.trim() ?? 'missing assessment'
      generated.push({ ...demoFindings[4], id: 'import-endpoint', sectionId: section.id, phrase })
    }

    setSections(importedSections)
    setFindings(generated)
    setAcceptedIds([])
    setActiveFindingId(generated[0]?.id ?? '')
    setNewProtocolOpen(false)
    setImportText('')
    setIsDemo(false)
    setTrialProfile(null)
    setView('review')
    setReviewing(true)
    window.setTimeout(() => {
      setReviewing(false)
      showToast(generated.length ? `Review complete · ${generated.length} signals found` : 'Review complete · no v0 rules matched')
    }, 1100)
  }

  function renderText(section: ProtocolSection) {
    const sectionFindings = unresolved.filter(
      (finding) => finding.sectionId === section.id && section.text.includes(finding.phrase),
    )
    if (!sectionFindings.length) return section.text

    const matches = sectionFindings
      .map((finding) => ({ finding, start: section.text.indexOf(finding.phrase), end: section.text.indexOf(finding.phrase) + finding.phrase.length }))
      .filter((match) => match.start >= 0)
      .sort((a, b) => a.start - b.start)

    const nodes: React.ReactNode[] = []
    let cursor = 0
    matches.forEach(({ finding, start, end }) => {
      if (start < cursor) return
      nodes.push(section.text.slice(cursor, start))
      nodes.push(
        <button
          className={`protocol-mark mark-${finding.severity.toLowerCase()} ${activeFindingId === finding.id ? 'is-active' : ''}`}
          key={finding.id}
          onClick={() => selectFinding(finding.id)}
          aria-label={`Review signal: ${finding.title}`}
        >
          {section.text.slice(start, end)}
        </button>,
      )
      cursor = end
    })
    nodes.push(section.text.slice(cursor))
    return nodes
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setView('review')} aria-label="Open Trialy review">
          <span className="brand-mark"><FlaskConical size={18} strokeWidth={2.2} /></span>
          <span>trialy</span>
          <span className="version-chip">v0</span>
        </button>
        <div className="protocol-identity">
          <span className="crumb">Protocol lab</span>
          <ChevronRight size={14} />
          <span className="protocol-name">{trialProfile?.nctId ?? (isDemo ? 'ORBIT-201' : 'Imported protocol')}</span>
          <span className="draft-state">Working draft</span>
        </div>
        <div className="top-actions">
          <a className="quiet-button" href="/">
            Optimizer dashboard
          </a>
          <button className="quiet-button" onClick={() => setNewProtocolOpen(true)}>
            <Upload size={16} /> Import protocol
          </button>
          <button
            className="primary-button"
            onClick={() => { setNctError(null); setNctLookupOpen(true) }}
            disabled={reviewing}
            aria-label={trialProfile ? 'Review another NCT' : 'Review an NCT'}
          >
            {reviewing ? <Activity className="spin-slow" size={16} /> : <Search size={16} />}
            {reviewing ? 'Reviewing…' : trialProfile ? 'Another NCT' : 'Review NCT'}
          </button>
        </div>
      </header>

      <aside className="rail" aria-label="Workspace navigation">
        <RailButton icon={<ClipboardCheck />} label="Review" active={view === 'review'} onClick={() => setView('review')} count={unresolved.length || undefined} />
        <RailButton icon={<BookOpen />} label="Evidence" active={view === 'evidence'} onClick={() => setView('evidence')} />
        <RailButton icon={<FileClock />} label="Versions" active={view === 'versions'} onClick={() => setView('versions')} />
        <div className="rail-spacer" />
        <button className="rail-button" title="Restore the sample protocol" onClick={resetDemo}>
          <RotateCcw size={19} />
          <span>Reset</span>
        </button>
      </aside>

      <main className="workspace">
        {view === 'review' && (
          <ReviewWorkspace
            sections={sections}
            renderText={renderText}
            unresolved={unresolved}
            acceptedIds={acceptedIds}
            directSources={directSources}
            supportSources={supportSources}
            visibleFindings={visibleFindings}
            activeFinding={activeFinding}
            activeFindingId={activeFindingId}
            activeEvidence={activeEvidence}
            activeDecisionEvidence={activeDecisionEvidence}
            filter={filter}
            setFilter={setFilter}
            selectFinding={selectFinding}
            acceptFinding={acceptFinding}
            reviewing={reviewing}
            isDemo={isDemo}
            trialProfile={trialProfile}
          />
        )}
        {view === 'evidence' && (
          <EvidenceLibrary
            query={evidenceQuery}
            setQuery={setEvidenceQuery}
            outcome={evidenceOutcome}
            setOutcome={setEvidenceOutcome}
            records={filteredEvidence}
          />
        )}
        {view === 'versions' && <Versions acceptedIds={acceptedIds} resetDemo={resetDemo} />}
      </main>

      {newProtocolOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setNewProtocolOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="import-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-heading">
              <div>
                <p className="eyebrow">New review</p>
                <h2 id="import-title">Paste a protocol excerpt</h2>
              </div>
              <button className="icon-button" onClick={() => setNewProtocolOpen(false)} aria-label="Close import dialog" title="Close">
                <X size={19} />
              </button>
            </div>
            <p className="modal-copy">The v0 rules recognize a small set of recruitment, visit-burden, combination-safety, and missing-assessment patterns.</p>
            <textarea
              autoFocus
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              placeholder="Paste protocol text here…"
              aria-label="Protocol text"
            />
            <div className="privacy-note"><ShieldCheck size={15} /> Prototype only. Do not paste confidential or patient-identifiable information.</div>
            <div className="modal-actions">
              <button className="quiet-button" onClick={() => setNewProtocolOpen(false)}>Cancel</button>
              <button className="primary-button" onClick={importProtocol} disabled={!importText.trim()}><Sparkles size={16} /> Review excerpt</button>
            </div>
          </div>
        </div>
      )}

      {nctLookupOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => !nctLoading && setNctLookupOpen(false)}>
          <form className="modal nct-modal" role="dialog" aria-modal="true" aria-labelledby="nct-title" onSubmit={reviewNct} onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-heading">
              <div>
                <p className="eyebrow">Registry review</p>
                <h2 id="nct-title">Review a specific NCT</h2>
              </div>
              <button type="button" className="icon-button" onClick={() => setNctLookupOpen(false)} aria-label="Close NCT lookup" title="Close" disabled={nctLoading}>
                <X size={19} />
              </button>
            </div>
            <p className="modal-copy">Trialy will load the current public ClinicalTrials.gov record and review its design, eligibility, outcomes, and enrollment plan.</p>
            <label className="nct-input-label">
              <span>NCT number or ClinicalTrials.gov URL</span>
              <span className={`nct-lookup-field ${nctError ? 'has-error' : ''}`}>
                <Search size={18} />
                <input
                  autoFocus
                  value={nctInput}
                  onChange={(event) => { setNctInput(event.target.value); setNctError(null) }}
                  placeholder="NCT02414139"
                  aria-describedby={nctError ? 'nct-error' : 'nct-source-note'}
                  disabled={nctLoading}
                />
              </span>
            </label>
            {nctError && <div className="nct-error" id="nct-error" role="alert"><CircleAlert size={15} /><span>{nctError}</span></div>}
            <div className="nct-examples"><span>Try a public study</span><button type="button" onClick={() => setNctInput('NCT02414139')}>NCT02414139</button><button type="button" onClick={() => setNctInput('NCT04139317')}>NCT04139317</button></div>
            <div className="registry-source-note" id="nct-source-note"><ShieldCheck size={16} /><span><b>Official public source</b>Loaded live through the ClinicalTrials.gov v2 API. Registry fields are sponsor-submitted and may not contain the full protocol.</span></div>
            <div className="modal-actions">
              <button type="button" className="quiet-button" onClick={() => setNctLookupOpen(false)} disabled={nctLoading}>Cancel</button>
              <button className="primary-button" type="submit" disabled={!nctInput.trim() || nctLoading}>
                {nctLoading ? <Activity className="spin-slow" size={16} /> : <Sparkles size={16} />}
                {nctLoading ? 'Loading registry…' : 'Load and review'}
              </button>
            </div>
          </form>
        </div>
      )}

      {toast && <div className="toast" role="status"><Check size={16} /> {toast}</div>}
    </div>
  )
}

interface RailButtonProps {
  icon: React.ReactNode
  label: string
  active: boolean
  count?: number
  onClick: () => void
}

function RailButton({ icon, label, active, count, onClick }: RailButtonProps) {
  return (
    <button className={`rail-button ${active ? 'is-active' : ''}`} onClick={onClick} title={label} aria-current={active ? 'page' : undefined}>
      {icon}
      <span>{label}</span>
      {count ? <b>{count}</b> : null}
    </button>
  )
}

interface ReviewWorkspaceProps {
  sections: ProtocolSection[]
  renderText: (section: ProtocolSection) => React.ReactNode
  unresolved: Finding[]
  acceptedIds: string[]
  directSources: number
  supportSources: number
  visibleFindings: Finding[]
  activeFinding?: Finding
  activeFindingId: string
  activeEvidence: typeof evidenceRecords
  activeDecisionEvidence: DecisionEvidence[]
  filter: 'All' | 'High'
  setFilter: (filter: 'All' | 'High') => void
  selectFinding: (id: string) => void
  acceptFinding: (finding: Finding) => void
  reviewing: boolean
  isDemo: boolean
  trialProfile: TrialProfile | null
}

function ReviewWorkspace({
  sections,
  renderText,
  unresolved,
  acceptedIds,
  directSources,
  supportSources,
  visibleFindings,
  activeFinding,
  activeFindingId,
  activeEvidence,
  activeDecisionEvidence,
  filter,
  setFilter,
  selectFinding,
  acceptFinding,
  reviewing,
  isDemo,
  trialProfile,
}: ReviewWorkspaceProps) {
  return (
    <div className="review-layout">
      <section className="document-pane">
        <div className="document-toolbar">
          <div className="document-title">
            <FileText size={18} />
            <div><strong>Protocol draft</strong><span>Structured review</span></div>
          </div>
          <div className="legend" aria-label="Annotation legend">
            <span><i className="legend-high" /> High</span>
            <span><i className="legend-moderate" /> Moderate</span>
            <span><i className="legend-review" /> Review</span>
          </div>
        </div>

        <div className="document-scroll">
          <article className="protocol-paper">
            <div className="paper-meta">
              <span>{trialProfile ? 'CLINICALTRIALS.GOV LIVE RECORD' : isDemo ? 'SYNTHETIC DEMONSTRATION' : 'IMPORTED EXCERPT'}</span>
              <span>{trialProfile ? `RECORD UPDATED · ${trialProfile.lastUpdated}` : 'VERSION 0.1 · 13 AUG 2026'}</span>
            </div>
            <h1>{trialProfile?.nctId ?? (isDemo ? 'ORBIT-201' : 'Imported protocol')}</h1>
            <p className="paper-subtitle">{trialProfile?.title ?? (isDemo ? 'A Phase 2 trial in MET-altered non-small cell lung cancer' : 'Protocol text under review')}</p>
            <div className="paper-rule" />
            {trialProfile && <TrialRegistrySnapshot profile={trialProfile} />}
            {sections.map((section) => {
              const count = unresolved.filter((finding) => finding.sectionId === section.id).length
              return (
                <section className="protocol-section" id={`section-${section.id}`} key={section.id}>
                  <div className={`evidence-spine ${count ? 'has-signals' : ''}`} aria-hidden="true">
                    {count ? <span>{count}</span> : <Check size={12} />}
                  </div>
                  <h2>{section.label}</h2>
                  <p>{renderText(section)}</p>
                </section>
              )
            })}
            <div className="paper-footer">
              <Info size={14} /> {trialProfile
                ? <span>This review is derived from public registry fields, not the full protocol. <a href={trialProfile.sourceUrl} target="_blank" rel="noreferrer">Open ClinicalTrials.gov <ArrowUpRight size={11} /></a></span>
                : isDemo
                  ? 'This is a synthetic protocol for product demonstration and is not suitable for clinical use.'
                  : 'This excerpt was reviewed by a small local v0 rule set and is not suitable for clinical decision-making.'}
            </div>
          </article>
        </div>
      </section>

      <aside className="review-pane">
        <div className="review-summary">
          <p className="eyebrow">Design review</p>
          <div className="review-title-row"><h2>{reviewing ? 'Tracing protocol logic…' : `${unresolved.length} open signals`}</h2><span className="evidence-count"><LinkIcon size={13} /> {directSources} analogs · {supportSources} source briefs</span></div>
          <div className="signal-track" aria-label={`${acceptedIds.length} accepted and ${unresolved.length} unresolved signals`}>
            {Array.from({ length: Math.max(1, acceptedIds.length + unresolved.length) }).map((_, index) => (
              <i key={index} className={index < acceptedIds.length ? 'resolved' : index < acceptedIds.length + unresolved.filter((item) => item.severity === 'High').length ? 'high' : 'open'} />
            ))}
          </div>
          <div className="filter-row">
            <button className={filter === 'All' ? 'is-active' : ''} onClick={() => setFilter('All')}>All signals</button>
            <button className={filter === 'High' ? 'is-active' : ''} onClick={() => setFilter('High')}>High priority</button>
            <span className="filter-icon" title="Results are ordered by severity"><ListFilter size={15} /></span>
          </div>
        </div>

        <div className="review-content">
          {reviewing ? (
            <ReviewingState />
          ) : !activeFinding || !visibleFindings.length ? (
            <EmptyReview hasFindings={unresolved.length > 0} hasTrialProfile={Boolean(trialProfile)} />
          ) : (
            <>
              <div className="finding-list" aria-label="Review signals">
                {visibleFindings.map((finding) => (
                  <button className={`finding-row ${activeFindingId === finding.id ? 'is-active' : ''}`} key={finding.id} onClick={() => selectFinding(finding.id)}>
                    <span className={`severity-dot severity-${finding.severity.toLowerCase()}`} />
                    <span><b>{finding.title}</b><small>{finding.category} · {finding.evidenceLabel}</small></span>
                    <ChevronRight size={16} />
                  </button>
                ))}
              </div>

              <div className="finding-detail">
                <div className="detail-kicker"><span className={`severity-pill severity-${activeFinding.severity.toLowerCase()}`}>{activeFinding.severity}</span><span>{activeFinding.category}</span><span>{activeFinding.confidence} confidence</span></div>
                <h3>{activeFinding.title}</h3>
                <p>{activeFinding.explanation}</p>

                <div className="recommendation-block">
                  <div className="recommendation-label"><Sparkles size={15} /> Suggested next move</div>
                  <p>{activeFinding.suggestion}</p>
                  {activeFinding.replacement && (
                    <div className="redline">
                      <span className="redline-before">{activeFinding.phrase}</span>
                      <span className="redline-after">{activeFinding.replacement}</span>
                    </div>
                  )}
                </div>

                <DecisionEvidenceChain records={activeDecisionEvidence} />

                {activeEvidence.length > 0 ? (
                  <div className="evidence-stack">
                    <div className="evidence-heading"><span>Outcome-aware analogs</span><span>{activeEvidence.length} record{activeEvidence.length > 1 ? 's' : ''}</span></div>
                    <div className="evidence-balance">
                      <EvidenceColumn
                        title="What worked"
                        tone="success"
                        records={activeEvidence.filter((record) => record.outcome === 'success')}
                      />
                      <EvidenceColumn
                        title="Where it broke"
                        tone="risk"
                        records={activeEvidence.filter((record) => record.outcome !== 'success')}
                      />
                    </div>
                    <p className="analogy-caveat"><Info size={13} /> Analogs support design reasoning; they do not establish that changing one clause will cause success.</p>
                  </div>
                ) : <p className="analogy-caveat no-analog-caveat"><Info size={13} /> No outcome-matched trial analog is attached; this decision is supported by the source briefs above and still requires expert review.</p>}

                <div className="detail-actions">
                  <button className="primary-button" onClick={() => acceptFinding(activeFinding)}>
                    <Check size={16} /> {activeFinding.replacement ? 'Apply suggestion' : 'Mark for review'}
                  </button>
                </div>
              </div>

              {acceptedIds.length > 0 && (
                <div className="scenario-strip">
                  <div className="scenario-heading"><div><SlidersHorizontal size={16} /><span>Illustrative scenario</span></div><small>Demo assumptions</small></div>
                  <div className="scenario-grid">
                    {scenarioMetrics.map((metric) => (
                      <div key={metric.label}><span>{metric.label}</span><b>{metric.before}</b><ChevronRight size={13} /><strong>{metric.after}</strong></div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

function ReviewingState() {
  return (
    <div className="reviewing-state">
      <div className="scan-orbit"><Activity size={26} /><i /><i /><i /></div>
      <h3>Comparing design choices</h3>
      <ul>
        <li><Check size={14} /> Structuring eligibility and endpoints</li>
        <li><Activity className="spin-slow" size={14} /> Matching registry precedents</li>
        <li className="waiting">Estimating operational pressure</li>
      </ul>
    </div>
  )
}

function TrialRegistrySnapshot({ profile }: { profile: TrialProfile }) {
  return (
    <section className="trial-registry-snapshot" aria-label="ClinicalTrials.gov study snapshot">
      <div className="registry-snapshot-grid">
        <div><span>Status</span><b>{profile.status}</b></div>
        <div><span>Phase</span><b>{profile.phase}</b></div>
        <div><span>Enrollment</span><b>{profile.enrollment ?? '—'} <small>{profile.enrollment !== null ? profile.enrollmentType : ''}</small></b></div>
        <div><span>Locations</span><b>{profile.locationCount}</b></div>
      </div>
      <div className="registry-strengths">
        <span>What the record does well</span>
        <ul>{profile.strengths.map((strength) => <li key={strength}><Check size={11} /> {strength}</li>)}</ul>
      </div>
    </section>
  )
}

function EmptyReview({ hasFindings, hasTrialProfile }: { hasFindings: boolean; hasTrialProfile: boolean }) {
  return (
    <div className="empty-review">
      <span><ClipboardCheck size={24} /></span>
      <h3>{hasFindings ? 'No high-priority signals' : hasTrialProfile ? 'No v0 design signals detected' : 'No v0 rules matched'}</h3>
      <p>{hasFindings
        ? 'Switch back to all signals to continue the review.'
        : hasTrialProfile
          ? 'The available registry fields passed this rule set. Review the full protocol and investigator materials before making a study decision.'
          : 'Try the ORBIT-201 sample or import a protocol containing a recognized pattern.'}</p>
    </div>
  )
}

function DecisionEvidenceChain({ records }: { records: DecisionEvidence[] }) {
  return (
    <section className="decision-evidence-stack" aria-label="Evidence supporting this decision">
      <div className="evidence-heading">
        <span>Why this decision is supported</span>
        <span>{records.length} source brief{records.length === 1 ? '' : 's'}</span>
      </div>
      <div className="decision-evidence-list" role="list">
        {records.map((record, index) => (
          <article className="decision-evidence-item" role="listitem" key={record.id}>
            <span className="evidence-chain-node" aria-hidden="true">
              {record.kind === 'Regulatory guidance' || record.kind === 'Methodology standard'
                ? <ShieldCheck size={13} />
                : record.kind === 'Trial result'
                  ? <FlaskConical size={13} />
                  : <BookOpen size={13} />}
            </span>
            <div className="decision-evidence-copy">
              <div className="decision-evidence-meta">
                <span className={`relevance-label relevance-${record.relevance.toLowerCase().replaceAll(' ', '-')}`}>{record.relevance}</span>
                <span>{record.kind} · {record.organization} · {record.year}</span>
              </div>
              <strong>{record.title}</strong>
              <p>{record.summary}</p>
              <div className="supports-claim"><Check size={12} /><span><b>Supports</b>{record.supports}</span></div>
              <p className="evidence-limitation"><Info size={11} /> {record.limitation}</p>
            </div>
            <a href={record.url} target="_blank" rel="noreferrer" aria-label={`Open source: ${record.title}`} title="Open source">
              Source <ArrowUpRight size={12} />
            </a>
            {index < records.length - 1 && <i className="chain-continuation" aria-hidden="true" />}
          </article>
        ))}
      </div>
    </section>
  )
}

function EvidenceColumn({ title, tone, records }: { title: string; tone: 'success' | 'risk'; records: EvidenceRecord[] }) {
  return (
    <div className={`evidence-column evidence-column-${tone}`}>
      <div className="evidence-column-title">
        <span>{tone === 'success' ? <Check size={13} /> : <CircleAlert size={13} />}{title}</span>
        <b>{records.length}</b>
      </div>
      {records.length ? records.map((record) => <AnalogCard key={record.id} record={record} />) : (
        <p className="evidence-column-empty">No direct analog attached to this side of the claim.</p>
      )}
    </div>
  )
}

function AnalogCard({ record }: { record: EvidenceRecord }) {
  return (
    <article className={`evidence-card outcome-${record.outcome}`}>
      <div className="evidence-card-top">
        <span className="mono">{record.nctId}</span>
        <span className={`outcome-badge outcome-${record.outcome}`}>{record.outcomeLabel}</span>
      </div>
      <strong>{record.title}</strong>
      <p>{record.result}</p>
      <div className="analog-tags">
        {record.analogs.slice(0, 3).map((analog) => <span key={analog}>{analog}</span>)}
      </div>
      <div className="source-links">
        {record.sources.map((source) => (
          <a key={`${record.id}-${source.label}`} href={source.url} target="_blank" rel="noreferrer">
            {source.label}<ArrowUpRight size={11} />
          </a>
        ))}
      </div>
    </article>
  )
}

function EvidenceLibrary({
  query,
  setQuery,
  outcome,
  setOutcome,
  records,
}: {
  query: string
  setQuery: (query: string) => void
  outcome: 'all' | TrialOutcome
  setOutcome: (outcome: 'all' | TrialOutcome) => void
  records: EvidenceRecord[]
}) {
  const outcomeCounts = {
    success: evidenceRecords.filter((record) => record.outcome === 'success').length,
    'endpoint-miss': evidenceRecords.filter((record) => record.outcome === 'endpoint-miss').length,
    stopped: evidenceRecords.filter((record) => record.outcome === 'stopped').length,
  }

  return (
    <div className="library-page">
      <header className="page-heading">
        <div><p className="eyebrow">Analog evidence library</p><h1>What worked. What failed. Why it matters.</h1><p>Same-disease, same-target, and same-backbone trials are kept distinct so a useful analogy never becomes a causal claim.</p></div>
        <div className="source-stamp"><ShieldCheck size={18} /><span><b>{evidenceRecords.length} linked trial analogs</b>Registry · publications · FDA</span></div>
      </header>
      <div className="library-controls">
        <label className="search-field"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search NCT ID, target, endpoint, or outcome" /></label>
        <div className="outcome-filters" aria-label="Filter by trial outcome">
          <button className={outcome === 'all' ? 'is-active' : ''} onClick={() => setOutcome('all')}>All <span>{evidenceRecords.length}</span></button>
          <button className={outcome === 'success' ? 'is-active' : ''} onClick={() => setOutcome('success')}>Succeeded <span>{outcomeCounts.success}</span></button>
          <button className={outcome === 'endpoint-miss' ? 'is-active' : ''} onClick={() => setOutcome('endpoint-miss')}>Endpoint miss <span>{outcomeCounts['endpoint-miss']}</span></button>
          <button className={outcome === 'stopped' ? 'is-active' : ''} onClick={() => setOutcome('stopped')}>Stopped early <span>{outcomeCounts.stopped}</span></button>
        </div>
      </div>
      <div className="analogy-key">
        <span><i className="key-success" /> Clinical or regulatory success</span>
        <span><i className="key-miss" /> Prespecified endpoint miss</span>
        <span><i className="key-stopped" /> Stopped before a clean efficacy conclusion</span>
      </div>
      <div className="evidence-table" role="table" aria-label="Successful and unsuccessful trial analogs">
        <div className="evidence-table-header" role="row"><span>Study</span><span>Outcome evidence</span><span>Why it is analogous</span><span>Sources</span></div>
        {records.map((record) => (
          <div className={`evidence-table-row outcome-${record.outcome}`} role="row" key={record.id}>
            <span className="study-cell"><b>{record.title}</b><small><span className="mono">{record.nctId}</span> · {record.phase} · {record.actualEnrollment} enrolled · {record.status.replaceAll('_', ' ')}</small></span>
            <span className="outcome-cell"><i className={`outcome-badge outcome-${record.outcome}`}>{record.outcomeLabel}</i><small>{record.result}</small></span>
            <span className="table-match"><b>{record.relevanceLabel}</b><small>{record.relevance}</small><span className="analog-tags">{record.analogs.slice(0, 3).map((analog) => <em key={analog}>{analog}</em>)}</span></span>
            <span className="table-sources">
              {record.sources.map((source) => (
                <a key={`${record.id}-${source.label}`} href={source.url} target="_blank" rel="noreferrer" title={`${source.kind}: ${source.label}`}>
                  {source.kind === 'Regulatory' ? <ShieldCheck size={13} /> : source.kind === 'Publication' ? <BookOpen size={13} /> : <LinkIcon size={13} />}
                  {source.label}
                </a>
              ))}
            </span>
          </div>
        ))}
        {!records.length && <div className="library-empty"><Search size={19} /><p><strong>No analogs match this view</strong>Clear the search or choose another outcome filter.</p></div>}
      </div>
      <div className="provenance-callout"><Info size={18} /><p><strong>How to read this evidence</strong>“Success” means the cited trial met its relevant endpoint or supported approval. “Stopped” is not treated as an efficacy failure. Convoke enrichment is not active yet because the supplied MCP endpoint requires an authenticated session.</p></div>
    </div>
  )
}

function Versions({ acceptedIds, resetDemo }: { acceptedIds: string[]; resetDemo: () => void }) {
  return (
    <div className="versions-page">
      <header className="page-heading">
        <div><p className="eyebrow">Working history</p><h1>Every suggestion leaves a trace.</h1><p>Versioning is local to this prototype. A production system would preserve author, rationale, source snapshot, and approval history.</p></div>
        <button className="quiet-button" onClick={resetDemo}><RotateCcw size={16} /> Restore sample</button>
      </header>
      <div className="version-timeline">
        {acceptedIds.length > 0 && (
          <div className="version-entry is-current"><span className="version-node"><Check size={15} /></span><div><div className="version-top"><b>Working draft</b><span>Just now</span></div><p>{acceptedIds.length} review suggestion{acceptedIds.length > 1 ? 's' : ''} incorporated. The redline is preserved in the review history.</p></div></div>
        )}
        <div className="version-entry"><span className="version-node"><FilePlus2 size={15} /></span><div><div className="version-top"><b>Version 0.1</b><span>Today, 12:22 PM</span></div><p>ORBIT-201 synthetic protocol imported for initial design review.</p><span className="version-tag">5 signals detected</span></div></div>
      </div>
      <div className="version-empty"><Layers3 size={22} /><div><b>Audit-ready version history</b><p>Reviewer identity, source revisions, and clinical approvals are intentionally out of scope for v0.</p></div></div>
    </div>
  )
}

export default App
