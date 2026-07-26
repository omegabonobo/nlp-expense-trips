# Business Trip Consolidation Requirements

This directory is the implementation backlog for turning NLP Expenses from an
assisted receipt-to-workbook tool into a complete, reviewable business-trip
consolidation workflow.

Each requirement is intentionally self-contained. A requirement can move to
`Implemented` only when its code, automated tests, user-facing behavior, and
documentation are complete.

## Delivery order

| ID | Requirement | Priority | Status | Depends on |
|---|---|---:|---|---|
| R001 | [Reconciliation freshness and job locking](R001-reconciliation-freshness-and-job-locking.md) | P0 | Implemented | — |
| R002 | [Invoice review and correction](R002-invoice-review-and-correction.md) | P0 | Implemented | R001 |
| R003 | [Manual CAD overrides and FX audit](R003-manual-cad-overrides-and-fx-audit.md) | P0 | Implemented | R002 |
| R007 | [Normalization warnings and duplicate resolution](R007-normalization-warnings-and-duplicate-resolution.md) | P0 | Implemented | R001 |
| R006 | [Statement coverage and completeness](R006-statement-coverage-and-completeness.md) | P1 | Implemented | R007 |
| R009 | [Unambiguous statement dates](R009-unambiguous-statement-dates.md) | P1 | Implemented | R006 |
| R008 | [HEIC and input-format capability detection](R008-heic-and-input-format-capabilities.md) | P1 | Implemented | — |
| R004 | [Split allocations, refunds, and credits](R004-split-allocations-refunds-and-credits.md) | P1 | Implemented | R002, R003 |
| R005 | [Accounting and tax profile](R005-accounting-and-tax-profile.md) | P1 | Implemented | R002 |
| R010 | [Trip metadata and policy controls](R010-trip-metadata-and-policy-controls.md) | P2 | Implemented | R005 |
| R011 | [Approval, archive, and audit trail](R011-approval-archive-and-audit-trail.md) | P2 | Implemented | R010 |
| R013 | [Shared line-item review and alcohol treatment](R013-shared-line-item-review-and-alcohol.md) | P1 | Implemented | R002, R005 |
| R014 | [Single receipt extraction choice](R014-single-receipt-extraction-choice.md) | P1 | Implemented | R013 |
| R012 | [IVADO frontend parity](R012-ivado-frontend-parity.md) | P2 | Implemented | R001–R009 |
| R015 | [App-first consolidation and finalization](R015-app-first-consolidation-and-finalization.md) | P0 | Implemented | R002–R014 |
| R016 | [Two-stage trip reimbursement and inter-project contract](R016-two-stage-trip-reimbursement-contract.md) | P0 | In progress | R010, R011, R013, R015 |

## Shared completion rules

Every requirement must:

1. preserve existing trip folders and workbooks;
2. migrate existing metadata without requiring manual file edits;
3. remain local-only unless the user explicitly selects OpenAI extraction;
4. make review states and warnings visible before workbook generation;
5. keep generated workbooks versioned and atomic;
6. add unit/service tests and Flask UI tests;
7. pass the full existing regression suite;
8. include a browser smoke test when visible UI changes are involved.

## Status definitions

- **Planned:** requirements are defined but implementation has not started.
- **In progress:** implementation or migration work is underway.
- **Implemented:** acceptance criteria are met and verified.
- **Deferred:** intentionally postponed with a documented reason.
