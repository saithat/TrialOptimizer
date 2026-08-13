# Outcome and causality model

## Three independent labels

Every trial/program assessment should answer three separate questions:

1. **Trial conduct status**: recruiting, completed, terminated, withdrawn, and so on.
2. **Endpoint result**: met, partially met, not met, harmed, not tested, or unknown.
3. **Program disposition**: advanced, approved, continued without advancement, paused,
   discontinued, rejected, withdrawn, or unknown.

Examples:

- A completed Phase 3 trial that missed its primary endpoint is a completed trial and an efficacy
  failure; the program might still continue in another population.
- A terminated trial can be a clinical success if it stopped early for overwhelming efficacy.
- A commercially discontinued program can have met its endpoints.

## Assessment outcome

Use these reviewable summary labels:

- `success`: prespecified primary objective met with acceptable benefit-risk and the relevant
  development decision is positive.
- `partial_success`: mixed/co-primary/subgroup result or evidence sufficient for a narrower path.
- `failure`: prespecified objective not met, unacceptable safety/benefit-risk, or a decisive
  regulatory rejection for the assessed program.
- `inconclusive`: underpowered, interrupted, corrupted, or otherwise unable to answer the question.
- `ongoing`: no assessable result yet.
- `unknown`: public evidence is insufficient.

Never collapse `inconclusive` or `unknown` into `failure` for model training.

## Causal factor taxonomy

An assessment may have multiple causal factors, each marked `primary`, `contributing`, or
`contextual`:

| Category | Examples |
|---|---|
| `biology` | target invalidity, insufficient pathway effect, resistance, disease heterogeneity |
| `efficacy` | effect too small, no dose response, durability failure |
| `safety` | toxicity, mortality imbalance, narrow therapeutic window |
| `dose_exposure` | inadequate exposure, PK variability, poor tissue penetration, immunogenicity |
| `population_selection` | biomarker strategy, line of therapy, disease stage, enrichment failure |
| `endpoint_choice` | insensitive endpoint, invalid surrogate, timing, multiplicity |
| `trial_design` | control/comparator, powering, estimand, crossover, protocol amendment |
| `operations` | enrollment, retention, site performance, data quality, supply execution |
| `manufacturing_quality` | CMC, comparability, device reliability, batch or inspection issue |
| `regulatory` | evidence package incomplete, confirmatory requirement, inspection deficiency |
| `commercial_strategy` | financing, portfolio reprioritization, market or competitive decision |
| `external` | pandemic, geopolitical disruption, acquisition, force majeure |

## Evidence strength

The database records confidence, but confidence is not source rank alone. Recommended starting
levels:

- `0.90-1.00`: regulator assessment/letter or complete, prespecified statistical result with direct
  linkage to the trial/program.
- `0.75-0.89`: peer-reviewed full report or detailed sponsor disclosure with consistent independent
  evidence.
- `0.50-0.74`: registry reason, conference abstract, SEC/sponsor statement with limited detail, or
  strong triangulation.
- `0.25-0.49`: analyst inference from timing, pipeline removal, or indirect documents.
- below `0.25`: hypothesis only; keep out of supervised labels.

Every factor should link to one or more supporting or contradicting `evidence_claim` records. A
human reviewer should approve high-impact labels and all primary causal factors used for training.

## Guardrails against false causality

- Preserve the publication date and retrieval date to prevent look-ahead leakage.
- Store contradicting claims instead of selecting one silently.
- Distinguish sponsor-stated reason from independently adjudicated cause.
- Do not interpret lack of a later-stage trial as proof of failure.
- Do not infer causality from FAERS disproportionality or social/news sentiment.
- Treat primary-endpoint changes after trial start as features and review flags, not automatic
  misconduct or failure.
- Train outcome and causal models only on evidence available as of the prediction cutoff.
