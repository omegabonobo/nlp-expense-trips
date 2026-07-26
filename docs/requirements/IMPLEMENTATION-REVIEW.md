# End-to-End Business Trip Consolidation Review

**Review date:** 2026-07-26
**Primary workflow:** Shared app-first review, with a canonical Arvine report,
an optional IVADO claim adapter, and a versioned inter-project manifest
**Result:** Both modes cover local consolidation from source collection through
editable results, finalization, versioned report-bundle export, and an approved
package. The workflow is ready for field testing; submission of an
IVADO-sponsored claim still requires confirmation of the current official IVADO
template and claimant/payee instruction.

## Capability coverage

| Stage | Implemented capability | Evidence / control |
|---|---|---|
| Trip setup | Create/select a local trip, choose claim program independently from the default receipt payer, and capture legal entities, traveller, dates, route, purpose, approver, settlement, expected accounts, and policy | `.nlp-expenses.json` remains the source of truth |
| Source collection | Copy validated receipts and statements into explicit trip folders without modifying originals | Safe paths, atomic uploads, content validation, duplicate detection, Finder actions |
| Statement interpretation | Preflight Arvine CSV/XLS/XLSX files and require explicit handling of ambiguous numeric dates | Provider, row count, warnings, errors, date samples, per-file convention |
| Reconciliation | Sync receipts and normalized card transactions in either mode, calculate statement-backed CAD totals and accounting FX rates | Persistent source fingerprint, automatic confidence, manual mappings |
| Exceptions | Correct invoice fields, add justified manual CAD, resolve possible duplicates, allocate split charges/refunds/fees/personal portions | Auditable reconciliation metadata and workbook statement rows |
| Completeness | Show provider/account/date coverage, expected accounts, overlap/duplicate indicators, trip-window gaps, and acknowledgement | Coverage confirmation is stored and written to Excel |
| Accounting | Apply a versioned trip snapshot for entity, registrant status, recovery/deduction percentages, counter-account, and journal mapping | Applied assumptions are embedded in each workbook |
| Policy | Flag category, meal-limit, and personal-allocation exceptions without silently changing data | Exception explanations remain visible in UI and workbook |
| App finalization | Review an issue queue, edit expense fields/person count/payer/Arvine eligibility/IVADO eligibility/lines, inspect CAD/FX and accounting results, then finalize the current input fingerprint | Any later source or decision change automatically reopens the review |
| Output | Always export a canonical Arvine report and `trip-reimbursement-manifest.v2.ndjson`; add a separate IVADO claim adapter for sponsored trips | All artifacts are generated from the same reviewed calculation and can be downloaded independently |
| Final review | Approve only a current generated input snapshot with complete metadata and no blocking review items | Separate approval record with SHA-256 hashes for every synchronized artifact |
| Retention | Export a ZIP with sources, reconciliation data, metadata, all generated artifacts, and compatibility plus approval manifests; archive/restore without moving files | Approval freshness changes automatically if any hashed artifact changes |
| Inter-project handoff | Emit contract `2.0.0` receipt and trip-report records with legal entities, payer, dual eligibility, accounting components, and explicit settlement legs | Producer schema validation plus consumer parser/CLI compatibility tests |

## Deliberate limitations and remaining gaps

1. **Statement coverage is evidence, not proof.** It compares observed
   transaction dates with the trip window; it cannot prove that a bank export
   contains every zero-activity day or every account unless the user registers
   expected accounts.
2. **No bank, card, accounting, travel-booking, or cloud integration.** Source
   files are manually exported and uploaded. The package is designed for
   handoff, not automatic posting.
3. **Mileage and per-diem rates are recorded, not used to create synthetic
   claims.** Supporting those claims requires a separate distance/day-entry
   workflow and supporting evidence.
4. **Detailed line extraction currently targets meals.** IVADO and Arvine now
   share persistent meal-line review, alcohol evidence, inclusion controls, and
   proportional Arvine claim/tax allocation. Hotel folios, airfare breakdowns,
   and other non-meal invoice lines remain total-level in the first scope.
5. **Tax controls are configurable assumptions, not tax advice.** The tool
   does not decide legal eligibility, filing treatment, or jurisdictional
   compliance.
6. **Manual Excel changes do not round-trip into the browser.** Excel is now an
   export of the finalized app state. Post-export edits are allowed and hashed
   at approval, but they are intentionally not imported back.
7. **Local single-user trust model.** Approval records provide reproducibility
   and change detection, not cryptographic identity, legal signatures, or
   protection from a user deliberately editing both source and manifest files.
8. **The IVADO workbook is a contract-backed adapter, not yet the confirmed
   official bilingual template.** The app records the template version and
   Arvine claimant/payee instruction, and warns until they are confirmed. Obtain
   the current official IVADO file and written submission instruction before
   treating the adapter as submission-ready.

## Recommended field acceptance

Run at least one representative trip per claim program. Include a
corporate-paid fallback, a foreign-currency
charge, a person split, excluded alcohol, a manual mapping, and a missing-field
correction. For Arvine, also include a split/personal transaction, refund,
duplicate export, and acknowledged coverage gap. Confirm the app preview,
both report outputs, shared NDJSON, and approval manifest agree before planning
external integrations.
