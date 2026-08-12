# Architecture

NLP Expenses is a local-first application. The Python package owns receipt and
statement processing, persisted review state, workbook generation, and the
localhost Flask interface. Runtime trip data is deliberately kept outside the
package and must never become a test fixture or distribution asset.

## Stable entry points

The following modules are compatibility facades. Scripts and integrations
should import from them instead of their implementation modules:

- `nlp_expenses.generator` — receipt extraction and report generation;
- `nlp_expenses.reconciliation` — reconciliation sync, mutations, and views;
- `nlp_expenses.workbook` — legacy, Arvine, and IVADO workbook builders;
- `nlp_expenses.ui` — Flask application creation and local-server startup.

The implementation is separated by responsibility:

| Area | Modules |
| --- | --- |
| Reconciliation | `reconciliation_state`, `reconciliation_allocations`, `reconciliation_coverage`, `reconciliation_invoices`, `reconciliation_views` |
| Workbooks | `workbook_common`, `workbook_arvine_detail`, `workbook_arvine`, `workbook_reports` |
| Web interface | `ui_routes_trips`, `ui_routes_reviews`, `ui_routes_ops`, `ui_services`, `ui_status` |
| Packaged contracts | `nlp_expenses.contracts` |

Implementation modules may change without requiring callers to update as long
as the facade behavior remains compatible.

## Processing flow

1. Receipt scans and statement exports are read from a validated trip folder.
2. Receipt review becomes the canonical editable expense model.
3. Reconciliation normalizes statement rows, applies decisions and allocations,
   and persists an atomic versioned snapshot.
4. Consolidation checks payer, employee-share, sponsor, and policy decisions.
5. Generation creates the primary workbook, manifest, and optional IVADO
   workbook from the finalized review.
6. Lifecycle records hash the complete generated bundle before approval and
   export.

State-writing modules use `nlp_expenses.storage` so readers never observe a
partially written JSON or settings file. Job locks protect mutations within a
single app process; separate app processes must not share one data root.

## Regression boundaries

`tests/test_characterization.py` protects behavior at the main refactoring
seams:

- the complete HTTP path and method inventory;
- normalized reconciliation totals, FX basis, coverage, and policy output;
- workbook formulas, styles, protection, validations, and hyperlinks through a
  semantic digest that ignores unstable XLSX ZIP metadata;
- cleanup of completed bundle artifacts when a later generation phase fails.

Every structural change should run `make check`. Changes that add modules,
package data, or entry points must also run `make package-check`. Private trip
acceptance checks remain opt-in through `make test-integration`.
