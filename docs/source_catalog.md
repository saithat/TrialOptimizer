# Public source catalog

Use sources in the order below. A stable first-party machine-readable source is preferred over
scraping. Bright Data is an acquisition layer, not an authority: every collected page must retain
its canonical URL, retrieval timestamp, and content hash.

## Tier 1: backbone and decisive evidence

| Source | What it contributes | Access | Important limitation |
|---|---|---|---|
| [ClinicalTrials.gov API v2](https://clinicaltrials.gov/data-api/api) | Protocol, status, arms, enrollment, outcomes, posted results, references | REST JSON/CSV | Registry status is not clinical success; missing negative results are common |
| [AACT](https://aact.ctti-clinicaltrials.org/) | Relational ClinicalTrials.gov mirror useful for bulk analysis | PostgreSQL/downloads | Still inherits registry omissions; use API/history snapshots for freshness and change tracking |
| [Drugs@FDA data files](https://www.fda.gov/drugs/drug-approvals-and-databases/drugsfda-data-files) | Applications, products, submissions, actions, labels, approval/review documents | Daily downloadable tables | Approval packages are richer than the tabular files and often require document parsing |
| [openFDA Complete Response Letters](https://open.fda.gov/apis/transparency/completeresponseletters/) | FDA deficiencies for approved and unapproved NDA/BLA applications | REST/bulk + PDFs | Coverage is expanding but not complete; redactions and older gaps remain |
| [EMA medicine/document JSON](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format) and EPARs | Approvals, refusals, withdrawals, assessment reports, product histories | JSON/tables + documents | Centrally evaluated products only; document linking and extraction required |
| [EU CTIS public portal](https://euclinicaltrials.eu/ctis-public/search) | EU protocols, products, endpoints, status, trial documents/results | Public portal | No stable public bulk API is assumed; verify terms and export options before automation |
| FDA/EMA review and advisory documents | Detailed efficacy, safety, statistical, CMC, and regulatory reasoning | Public HTML/PDF | Mapping application documents back to trials/assets needs curated identifiers |

## Tier 2: result and causal context

| Source | Contribution | Access / recommended use |
|---|---|---|
| [PubMed E-utilities](https://pubmed.ncbi.nlm.nih.gov/download/) and PubMed Central | Trial publications, protocols, subgroup analyses, meta-analyses | API/bulk; prioritize NCT ID and DOI links, then entity/title matching |
| Sponsor press releases and investor relations pages | Top-line endpoint results, discontinuations, operational and strategic reasons | Bright Data only when a first-party feed/API is unavailable; archive the exact page |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 8-K, 10-Q, 10-K, 20-F, 6-K disclosures; program terminations; risk and financing context | Direct SEC APIs/archives; do not scrape via Bright Data when EDGAR provides the filing |
| Conference abstracts/posters | Results before full publication, subgroup and safety detail | Society sites and DOI sources; Bright Data for public pages lacking APIs |
| Patents and sponsor pipeline pages | Asset synonyms, ownership, combinations, formulation and mechanism context | Public patent sources and archived sponsor pages; not outcome evidence alone |
| Grants and investigator sources | Funding termination, academic programs, linked work | NIH RePORTER and other funder APIs where available |

## Tier 3: normalization and post-market signals

| Source | Contribution | Key caveat |
|---|---|---|
| [DailyMed API](https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm) | Current/archived structured labels and NDA links | Label text reflects approved use, not every failed program |
| [openFDA drug endpoints](https://open.fda.gov/apis/drug/) / FAERS | Labels, recalls, enforcement, Drugs@FDA, adverse-event reports | FAERS supports signal detection, not incidence or causal attribution |
| [PubChem PUG REST](https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest) | Chemical identity, structure, synonyms, identifiers | Mixtures, salts, stereochemistry, and biologics require extra handling |
| [ChEMBL](https://www.ebi.ac.uk/chembl/) | Molecules, mechanisms, targets, bioactivity, clinical candidates | Public curation may lag the latest sponsor disclosures |
| [Mondo](https://mondo.monarchinitiative.org/pages/download/) + MeSH/NCIt | Disease normalization and cross-references | Preserve source wording as well as normalized concepts |
| Orange Book / Purple Book / orphan designations | Approval, exclusivity, biologic, and designation context | Designation is not evidence of efficacy or approval |
| WHO ICTRP and primary national registries | Non-US/global trial discovery | WHO data reuse conditions and registry access differ; deduplicate across IDs |

## Tier 4: explanatory features, not causal verdicts

Use [Open Targets](https://platform.opentargets.org/), GWAS Catalog, GTEx, DepMap, Reactome,
UniProt, PharmGKB, and ChEMBL bioactivity to construct target, pathway, genetic-support, tissue,
exposure, and mechanism features. These can help explain or predict outcomes, but none should be
stored as proof that a particular program failed for a biological reason.

The open [Clinical Trial Outcome Database benchmark](https://chufangao.github.io/CTOD/) can be used
to compare labeling and modeling approaches. Its outcome labels and LLM interpretations remain a
secondary research dataset; they should be reconciled back to primary evidence before entering the
reviewed outcome table.

## Bright Data collection policy

Use Bright Data for explicitly public pages when robots/terms and organizational policy permit it.
Prefer asynchronous snapshot collection for batches, retain raw HTML or WARC when valuable, and
deliver to durable object storage. Record the Bright Data snapshot ID in `source_document.metadata`.

Recommended initial collectors:

1. sponsor press releases and pipeline pages for assets already in the database;
2. conference abstract/poster pages linked to known trials;
3. public investor presentations and earnings-call pages;
4. public regulatory pages only when no supported first-party download exists.

Do not use Bright Data to bypass authentication, paywalls, access controls, or contractual limits.
