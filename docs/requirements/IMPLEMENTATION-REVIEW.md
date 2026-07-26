# End-to-End Business Trip Consolidation Review

**Review date:** 2026-07-25  
**Primary workflow:** Shared app-first review, with Arvine accounting and IVADO
workbook adapters  
**Result:** Both modes cover local consolidation from source collection through
editable results, finalization, versioned Excel export, and an approved package.

## Capability coverage

| Stage | Implemented capability | Evidence / control |
|---|---|---|
| Trip setup | Create/select a local trip, choose mode, capture traveller, company, dates, route, purpose, project, cost centre, approver, payment method, expected accounts, and policy | `.nlp-expenses.json` remains the source of truth |
| Source collection | Copy validated receipts and statements into explicit trip folders without modifying originals | Safe paths, atomic uploads, content validation, duplicate detection, Finder actions |
| Statement interpretation | Preflight Arvine CSV/XLS/XLSX files and require explicit handling of ambiguous numeric dates | Provider, row count, warnings, errors, date samples, per-file convention |
| Reconciliation | Sync receipts and normalized card transactions in either mode, calculate statement-backed CAD totals and accounting FX rates | Persistent source fingerprint, automatic confidence, manual mappings |
| Exceptions | Correct invoice fields, add justified manual CAD, resolve possible duplicates, allocate split charges/refunds/fees/personal portions | Auditable reconciliation metadata and workbook statement rows |
| Completeness | Show provider/account/date coverage, expected accounts, overlap/duplicate indicators, trip-window gaps, and acknowledgement | Coverage confirmation is stored and written to Excel |
| Accounting | Apply a versioned trip snapshot for entity, registrant status, recovery/deduction percentages, counter-account, and journal mapping | Applied assumptions are embedded in each workbook |
| Policy | Flag category, meal-limit, and personal-allocation exceptions without silently changing data | Exception explanations remain visible in UI and workbook |
| App finalization | Review an issue queue, edit expense fields/person count/whole inclusion/lines, inspect CAD/FX and accounting results, then finalize the current input fingerprint | Any later source or decision change automatically reopens the review |
| Output | Export the finalized app state to an atomic, versioned Excel workbook and preserve earlier/manual versions | Open, reveal, and download actions |
| Final review | Approve only a current generated input snapshot with complete metadata and no blocking Arvine review items | Separate approval record with SHA-256 hashes |
| Retention | Export a ZIP with sources, reconciliation data, metadata, workbook, and manifest; archive/restore without moving files | Approval freshness changes automatically if any hashed artifact changes |

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

## Recommended field acceptance

Run at least one representative trip per mode. Include a foreign-currency
charge, a person split, excluded alcohol, a manual mapping, and a missing-field
correction. For Arvine, also include a split/personal transaction, refund,
duplicate export, and acknowledged coverage gap. Confirm the app preview,
Excel output, and approval manifest agree before planning external integrations.
