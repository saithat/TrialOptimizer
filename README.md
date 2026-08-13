# Trialy v0

Trialy is an interactive prototype for evidence-backed clinical-trial protocol review. It behaves like a focused protocol editor: risky clauses are marked in place, each signal explains its reasoning, and public registry precedents remain attached to the suggestion.

## Run it

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## What works

- Live NCT lookup through the official ClinicalTrials.gov v2 study endpoint.
- Registry-specific feedback generated from study design, cohort count, eligibility, outcomes, enrollment, locations, and results availability.
- A synthetic Phase 2 NSCLC protocol with five clickable design signals.
- Recruitment, eligibility, safety, operations, and endpoint review categories.
- Redlined suggestions that modify the working protocol when accepted.
- A clearly labeled illustrative before/after scenario.
- A searchable evidence library containing nine linked successful, endpoint-miss, and early-stop trial analogs.
- Side-by-side “what worked” and “where it broke” evidence behind applicable review claims.
- A decision-level evidence chain for every signal, separating direct precedent, design standards, and supporting context.
- Fifteen source briefs with a plain-language claim supported and an explicit limitation.
- Explicit same-disease, same-target, same-backbone, and same-strategy analogy labels.
- Direct ClinicalTrials.gov, peer-reviewed publication, and FDA links where available.
- Paste-in review for a small local rule set.
- Working-draft history and responsive mobile layout.

## Evidence boundary

The protocol and scenario outputs are synthetic. The NCT records and registry statuses are real source links captured for the prototype, while the relevance labels are qualitative interface-demo judgments—not validated clinical similarity scores. Registry explanations are sponsor-submitted and are not treated as complete causal explanations. A stopped trial is kept separate from a completed trial that missed its prespecified endpoint.

The supplied Convoke MCP endpoint currently redirects anonymous requests to sign-in, so Convoke enrichment is presented as a future authenticated source rather than implied to be active.

The local Vite development and preview servers proxy `/api/clinicaltrials/*` to the official ClinicalTrials.gov API because the registry response does not expose browser CORS headers. A production host must preserve that same-origin proxy route.

This prototype is research workflow software, not clinical, statistical, regulatory, or medical advice. Do not paste confidential or patient-identifiable information.
